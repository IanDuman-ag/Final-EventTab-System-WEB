import subprocess
import sys
import os

def run_django_cmd(args):
    """
    Runs a Django management command using the project's manage.py.
    """
    # Get the project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manage_py = os.path.join(project_root, 'manage.py')
    
    # Path to the virtual environment python
    if os.name == 'nt':
        python_exe = os.path.join(project_root, 'venv', 'Scripts', 'python.exe')
    else:
        python_exe = os.path.join(project_root, 'venv', 'bin', 'python')
        
    if not os.path.exists(python_exe):
        # Fallback to current python if venv is not found
        python_exe = sys.executable

    cmd = [python_exe, manage_py] + args
    
    print(f"Executing: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(result.stdout)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
        print(e.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python django_cmd.py <command> [args...]")
        sys.exit(1)
        
    run_django_cmd(sys.argv[1:])
