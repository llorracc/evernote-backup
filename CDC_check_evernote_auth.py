#!/usr/bin/env python3
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

def run_with_sudo(cmd: List[str]) -> subprocess.CompletedProcess:
    """Run a command with sudo, prompting for password if needed."""
    if os.geteuid() == 0:  # If already running as root
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        sudo_cmd = ["sudo"] + cmd
        return subprocess.run(sudo_cmd, capture_output=True, text=True)

def read_file_with_sudo(file_path: Path) -> Optional[bytes]:
    """Read a file with sudo if regular access fails."""
    try:
        # First try regular access
        with open(file_path, 'rb') as f:
            return f.read()
    except (PermissionError, FileNotFoundError):
        try:
            # If permission denied or file not found, try with sudo
            result = run_with_sudo(["cat", str(file_path)])
            if result.returncode == 0:
                return result.stdout.encode()
            return None
        except Exception as e:
            print(f"Error reading file with sudo: {e}")
            return None
    except Exception as e:
        print(f"Error reading file: {e}")
        return None

def check_directory_exists_with_sudo(dir_path: Path) -> bool:
    """Check if a directory exists with sudo."""
    try:
        # First try regular access
        return dir_path.exists()
    except PermissionError:
        try:
            # If permission denied, try with sudo
            result = run_with_sudo(["test", "-d", str(dir_path)])
            return result.returncode == 0
        except Exception as e:
            print(f"Error checking directory with sudo: {e}")
            return False
    except Exception as e:
        print(f"Error checking directory: {e}")
        return False

def list_directory_with_sudo(dir_path: Path) -> List[Path]:
    """List directory contents with sudo."""
    try:
        # First try regular access
        return list(dir_path.iterdir())
    except (PermissionError, FileNotFoundError):
        try:
            # If permission denied or directory not found, try with sudo
            result = run_with_sudo(["ls", "-1", str(dir_path)])
            if result.returncode == 0:
                return [dir_path / line.strip() for line in result.stdout.splitlines()]
            return []
        except Exception as e:
            print(f"Error listing directory with sudo: {e}")
            return []
    except Exception as e:
        print(f"Error listing directory: {e}")
        return []

class EvernoteAuthChecker:
    def __init__(self, evernote_dir: Optional[Path] = None):
        self.evernote_dir = evernote_dir
        if evernote_dir:
            self.accounts_dir = evernote_dir / "accounts" / "www.evernote.com"
            self.bootstrap_dir = evernote_dir / "bootstrap"
        
    def find_evernote_dirs(self) -> List[Path]:
        """Find all com.evernote.Evernote directories using sudo mdfind."""
        try:
            # First try system-wide search with sudo
            result = run_with_sudo(["mdfind", "-0", "kMDItemFSName == 'com.evernote.Evernote'"])
            
            if result.returncode == 0:
                # Split on null bytes to handle paths with newlines
                paths = [p for p in result.stdout.split('\0') if p]
                return [Path(path.strip()) for path in paths]
            
            print("Warning: sudo mdfind failed, falling back to user-level search")
            # Fallback to regular user search
            result = subprocess.run(
                ["mdfind", "-0", "kMDItemFSName == 'com.evernote.Evernote'"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                paths = [p for p in result.stdout.split('\0') if p]
                return [Path(path.strip()) for path in paths]
            return []
        except Exception as e:
            print(f"Error running mdfind: {e}")
            return []

    def check_props_files(self) -> List[Tuple[Path, Dict]]:
        """Check props files for authentication tokens."""
        results = []
        if not check_directory_exists_with_sudo(self.accounts_dir):
            return results
            
        try:
            # Use sudo find to get all props files
            find_cmd = ["find", str(self.accounts_dir), "-name", "props", "-type", "f"]
            result = run_with_sudo(find_cmd)
            
            if result.returncode != 0:
                print(f"Error finding props files: {result.stderr}")
                return results
                
            props_files = [Path(line.strip()) for line in result.stdout.splitlines()]
            
            for props_file in props_files:
                try:
                    content = read_file_with_sudo(props_file)
                    if not content:
                        continue
                        
                    # Common auth token patterns
                    token_patterns = [
                        r'[A-Za-z0-9+/]{32,}={0,2}',  # Base64-like strings
                        r'[A-Za-z0-9]{32,}',           # Long alphanumeric strings
                        r'[A-Za-z0-9]{8}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{12}'  # UUID pattern
                    ]
                    
                    potential_tokens = []
                    for pattern in token_patterns:
                        matches = re.findall(pattern.encode(), content)
                        potential_tokens.extend(m.decode() for m in matches)
                    
                    if potential_tokens:
                        # Get file size with sudo
                        size_result = run_with_sudo(["stat", "-f", "%z", str(props_file)])
                        file_size = int(size_result.stdout.strip()) if size_result.returncode == 0 else 0
                        
                        results.append((props_file, {
                            'potential_tokens': potential_tokens,
                            'file_size': file_size
                        }))
                except Exception as e:
                    print(f"Error processing {props_file}: {e}")
                    
        except Exception as e:
            print(f"Error in check_props_files: {e}")
            
        return results

    def check_local_note_store(self) -> List[Tuple[Path, Dict]]:
        """Check LocalNoteStore.sqlite for authentication information."""
        results = []
        local_store = self.accounts_dir / "localNoteStore" / "LocalNoteStore.sqlite"
        
        if not check_directory_exists_with_sudo(local_store.parent):
            return results
            
        try:
            # Try to copy the database file to a temporary location with sudo
            temp_db = Path("/tmp/temp_notestore.sqlite")
            copy_cmd = ["cp", str(local_store), str(temp_db)]
            copy_result = run_with_sudo(copy_cmd)
            
            if copy_result.returncode != 0:
                print(f"Error copying database: {copy_result.stderr}")
                return results
                
            # Change permissions to allow reading
            chmod_cmd = ["chmod", "644", str(temp_db)]
            chmod_result = run_with_sudo(chmod_cmd)
            
            if chmod_result.returncode != 0:
                print(f"Error changing permissions: {chmod_result.stderr}")
                return results
            
            try:
                conn = sqlite3.connect(str(temp_db))
                cursor = conn.cursor()
                
                # Check for auth-related tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [row[0] for row in cursor.fetchall()]
                
                auth_related_tables = []
                for table in tables:
                    if any(keyword in table.lower() for keyword in ['auth', 'token', 'session', 'user']):
                        cursor.execute(f"SELECT COUNT(*) FROM {table};")
                        count = cursor.fetchone()[0]
                        auth_related_tables.append({
                            'name': table,
                            'row_count': count
                        })
                        
                if auth_related_tables:
                    # Get file size with sudo
                    size_result = run_with_sudo(["stat", "-f", "%z", str(local_store)])
                    file_size = int(size_result.stdout.strip()) if size_result.returncode == 0 else 0
                    
                    results.append((local_store, {
                        'auth_tables': auth_related_tables,
                        'file_size': file_size
                    }))
                    
                conn.close()
            finally:
                # Clean up temporary file
                os.remove(str(temp_db))
                
        except Exception as e:
            print(f"Error reading {local_store}: {e}")
            
        return results

    def check_bootstrap(self) -> List[Tuple[Path, Dict]]:
        """Check bootstrap directory for auth-related files."""
        results = []
        if not check_directory_exists_with_sudo(self.bootstrap_dir):
            return results
            
        # Look for files that might contain auth info
        auth_related_files = [
            'user.json',
            'session.json',
            'auth.json',
            '*.plist'
        ]
        
        for pattern in auth_related_files:
            try:
                find_cmd = ["find", str(self.bootstrap_dir), "-name", pattern, "-type", "f"]
                result = run_with_sudo(find_cmd)
                
                if result.returncode != 0:
                    continue
                    
                for file in [Path(f.strip()) for f in result.stdout.splitlines()]:
                    try:
                        content = read_file_with_sudo(file)
                        if not content:
                            continue
                            
                        # Check for JSON content
                        try:
                            json_content = json.loads(content)
                            if any(keyword in str(json_content).lower() 
                                  for keyword in ['auth', 'token', 'session', 'user']):
                                # Get file size with sudo
                                size_result = run_with_sudo(["stat", "-f", "%z", str(file)])
                                file_size = int(size_result.stdout.strip()) if size_result.returncode == 0 else 0
                                
                                results.append((file, {
                                    'type': 'json',
                                    'content': json_content,
                                    'file_size': file_size
                                }))
                        except json.JSONDecodeError:
                            # Not JSON, check for binary patterns
                            token_patterns = [
                                r'[A-Za-z0-9+/]{32,}={0,2}',
                                r'[A-Za-z0-9]{32,}',
                                r'[A-Za-z0-9]{8}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{12}'
                            ]
                            
                            potential_tokens = []
                            for pattern in token_patterns:
                                matches = re.findall(pattern.encode(), content)
                                potential_tokens.extend(m.decode() for m in matches)
                                
                            if potential_tokens:
                                # Get file size with sudo
                                size_result = run_with_sudo(["stat", "-f", "%z", str(file)])
                                file_size = int(size_result.stdout.strip()) if size_result.returncode == 0 else 0
                                
                                results.append((file, {
                                    'type': 'binary',
                                    'potential_tokens': potential_tokens,
                                    'file_size': file_size
                                }))
                    except Exception as e:
                        print(f"Error reading {file}: {e}")
                        
            except Exception as e:
                print(f"Error processing pattern {pattern}: {e}")
                    
        return results

    def check_keychain(self) -> Dict:
        """Check MacOS keychain for Evernote credentials."""
        results = {
            'found': False,
            'items': []
        }
        
        try:
            # Try with sudo first for system keychain
            result = run_with_sudo(["security", "find-internet-password", "-g", "-s", "evernote.com"])
            
            if result.returncode == 0:
                results['found'] = True
                results['items'].append({
                    'type': 'internet_password (system)',
                    'details': result.stdout
                })
            
            # Then check user keychain
            result = subprocess.run(
                ["security", "find-internet-password", "-g", "-s", "evernote.com"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                results['found'] = True
                results['items'].append({
                    'type': 'internet_password (user)',
                    'details': result.stdout
                })
                
            # Check system keychain for generic passwords
            result = run_with_sudo(["security", "find-generic-password", "-g", "-s", "Evernote"])
            
            if result.returncode == 0:
                results['found'] = True
                results['items'].append({
                    'type': 'generic_password (system)',
                    'details': result.stdout
                })
                
            # Check user keychain for generic passwords
            result = subprocess.run(
                ["security", "find-generic-password", "-g", "-s", "Evernote"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                results['found'] = True
                results['items'].append({
                    'type': 'generic_password (user)',
                    'details': result.stdout
                })
                
        except Exception as e:
            print(f"Error checking keychain: {e}")
            
        return results

    def analyze(self) -> Dict:
        """Run all checks and return results."""
        results = {
            'props_files': [],
            'local_note_store': [],
            'bootstrap': []
        }
        
        if self.evernote_dir:
            results.update({
                'props_files': self.check_props_files(),
                'local_note_store': self.check_local_note_store(),
                'bootstrap': self.check_bootstrap()
            })
        
        # Determine if any valid auth tokens were found
        has_potential_auth = any(
            len(files) > 0 for files in results.values() if isinstance(files, list)
        )
        
        results['has_potential_auth'] = has_potential_auth
        return results

def format_results(results: Dict, dir_path: Path) -> str:
    """Format the results in a human-readable way."""
    output = []
    
    if not results['has_potential_auth']:
        output.append(f"No potential authentication tokens found in {dir_path}.")
        return "\n".join(output)
        
    output.append(f"Potential authentication information found in {dir_path}:")
    
    # Props files
    if results['props_files']:
        output.append("\nProps files:")
        for file, data in results['props_files']:
            output.append(f"  {file}")
            output.append(f"    Size: {data['file_size']} bytes")
            output.append(f"    Potential tokens: {len(data['potential_tokens'])}")
            
    # Local Note Store
    if results['local_note_store']:
        output.append("\nLocal Note Store:")
        for file, data in results['local_note_store']:
            output.append(f"  {file}")
            output.append(f"    Size: {data['file_size']} bytes")
            output.append("    Auth-related tables:")
            for table in data['auth_tables']:
                output.append(f"      {table['name']}: {table['row_count']} rows")
                
    # Bootstrap
    if results['bootstrap']:
        output.append("\nBootstrap files:")
        for file, data in results['bootstrap']:
            output.append(f"  {file}")
            output.append(f"    Size: {data['file_size']} bytes")
            if data['type'] == 'json':
                output.append("    Contains JSON with auth-related fields")
            else:
                output.append(f"    Potential tokens: {len(data['potential_tokens'])}")
                
    return "\n".join(output)

def format_keychain_results(results: Dict) -> str:
    """Format keychain results in a human-readable way."""
    output = []
    
    if not results['found']:
        output.append("No Evernote credentials found in keychain.")
        return "\n".join(output)
        
    output.append("Found Evernote credentials in keychain:")
    for item in results['items']:
        # Extract just the keychain path and account
        details = item['details']
        keychain = re.search(r'keychain: "([^"]+)"', details)
        account = re.search(r'"acct"<blob>="([^"]+)"', details)
        
        if keychain and account:
            output.append(f"\n{item['type']}:")
            output.append(f"  Keychain: {keychain.group(1)}")
            output.append(f"  Account: {account.group(1)}")
                
    return "\n".join(output)

def generate_summary(evernote_dirs: List[Path], all_results: List[Tuple[Path, Dict]], keychain_results: Dict) -> str:
    """Generate a summary of all findings."""
    output = []
    output.append("\nSUMMARY")
    output.append("=======")
    
    # Summarize directories by user
    users = {}
    for dir_path in evernote_dirs:
        user = dir_path.parts[2]  # /Users/username/...
        if user not in users:
            users[user] = []
        users[user].append(dir_path)
    
    output.append(f"\nFound {len(evernote_dirs)} Evernote directories across {len(users)} users:")
    for user, dirs in users.items():
        output.append(f"\n{user}:")
        for dir_path in dirs:
            output.append(f"  {dir_path}")
    
    # Summarize auth findings
    auth_found = []
    for dir_path, results in all_results:
        if results['has_potential_auth']:
            auth_found.append(dir_path)
    
    if auth_found:
        output.append("\nPotential authentication tokens found in:")
        for dir_path in auth_found:
            output.append(f"  {dir_path}")
    else:
        output.append("\nNo authentication tokens found in filesystem.")
    
    # Summarize keychain findings
    if keychain_results['found']:
        output.append("\nKeychain entries found:")
        for item in keychain_results['items']:
            details = item['details']
            account = re.search(r'"acct"<blob>="([^"]+)"', details)
            if account:
                output.append(f"  {item['type']}: {account.group(1)}")
    else:
        output.append("\nNo keychain entries found.")
    
    return "\n".join(output)

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Check Evernote files for authentication tokens")
    parser.add_argument("--evernote-dir", help="Path to specific Evernote application support directory")
    args = parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        print("Note: This script will use sudo to search system-wide. You may be prompted for your password.")
    
    checker = EvernoteAuthChecker()
    
    # First find all Evernote directories
    evernote_dirs = checker.find_evernote_dirs()
    if not evernote_dirs:
        print("No Evernote directories found on the system.")
        return
        
    print(f"\nFound {len(evernote_dirs)} Evernote directories:")
    for dir_path in evernote_dirs:
        print(f"  {dir_path}")
    
    # Check each directory
    all_results = []
    for dir_path in evernote_dirs:
        print(f"\nChecking directory: {dir_path}")
        checker.evernote_dir = dir_path
        checker.accounts_dir = dir_path / "accounts" / "www.evernote.com"
        checker.bootstrap_dir = dir_path / "bootstrap"
        
        results = checker.analyze()
        all_results.append((dir_path, results))
        
        print(format_results(results, dir_path))
    
    # Check keychain once at the end
    print("\nChecking MacOS keychain for Evernote credentials...")
    keychain_results = checker.check_keychain()
    print(format_keychain_results(keychain_results))
    
    # Print summary
    print(generate_summary(evernote_dirs, all_results, keychain_results))

if __name__ == "__main__":
    main() 