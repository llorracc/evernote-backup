#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

def run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    print(f"\nRunning: {cmd}")
    result = subprocess.run(cmd, shell=True, check=check, text=True)
    return result

def get_user_input(prompt: str, default: Optional[str] = None) -> str:
    """Get user input with an optional default value."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    response = input(prompt).strip()
    return response if response else default

def main():
    print("""
    Evernote Export Script
    =====================
    
    This script will help you export your Evernote notes for use with other applications.
    The process involves:
    1. Initializing a local database
    2. Syncing your notes from Evernote
    3. Exporting the notes in ENEX format
    
    The exported notes will be compatible with Obsidian, Joplin, and other note-taking apps.
    """)

    # Get user preferences
    output_dir = get_user_input(
        "Where would you like to save the exported notes?",
        str(Path.home() / "evernote_export")
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Initialize database
    print("\nStep 1: Initializing database...")
    db_path = output_path / "evernote.db"
    run_command(f"evernote-backup init-db --database {db_path}")

    # Sync notes
    print("\nStep 2: Syncing notes from Evernote...")
    print("This may take some time depending on the number of notes.")
    run_command(f"evernote-backup sync --database {db_path}")

    # Export notes
    print("\nStep 3: Exporting notes...")
    export_options = [
        "--single-notes",  # One file per note
        "--add-guid",      # Add GUIDs for better tracking
        "--add-metadata",  # Include metadata
        "--overwrite"      # Overwrite existing files
    ]
    export_cmd = f"evernote-backup export --database {db_path} {' '.join(export_options)} {output_path}"
    run_command(export_cmd)

    print(f"""
    Export complete!
    
    Your notes have been exported to: {output_path}
    
    Next steps:
    1. Review the exported notes in the output directory
    2. Import the ENEX files into your preferred note-taking app
    3. If needed, adjust formatting or structure in the new app
    
    Note: The exported notes are in ENEX format, which is compatible with:
    - Obsidian (via the Evernote importer plugin)
    - Joplin (via the ENEX import feature)
    - Other note-taking apps that support ENEX format
    """)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExport cancelled by user.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed with exit code {e.returncode}")
        print(f"Command: {e.cmd}")
        print(f"Output: {e.output}")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {str(e)}")
        sys.exit(1) 