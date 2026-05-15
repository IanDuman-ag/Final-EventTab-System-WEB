import os
import sys
import subprocess

def create_app_structure(app_name):
    """
    Creates a standard Django app structure with models, views, urls, and templates.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.join(project_root, app_name)
    
    if os.path.exists(app_dir):
        print(f"Error: Directory '{app_name}' already exists.")
        sys.exit(1)
        
    # 1. Run startapp
    # Path to the virtual environment python
    if os.name == 'nt':
        python_exe = os.path.join(project_root, 'venv', 'Scripts', 'python.exe')
    else:
        python_exe = os.path.join(project_root, 'venv', 'bin', 'python')
        
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    manage_py = os.path.join(project_root, 'manage.py')
    
    print(f"Creating Django app: {app_name}")
    subprocess.run([python_exe, manage_py, 'startapp', app_name], check=True)
    
    # 2. Create urls.py
    urls_py = os.path.join(app_dir, 'urls.py')
    with open(urls_py, 'w') as f:
        f.write("from django.urls import path\nfrom . import views\n\nurlpatterns = [\n    # path('', views.index, name='index'),\n]\n")
        
    # 3. Create templates directory
    templates_dir = os.path.join(app_dir, 'templates', app_name)
    os.makedirs(templates_dir, exist_ok=True)
    
    # 4. Create an initial index.html
    index_html = os.path.join(templates_dir, 'index.html')
    with open(index_html, 'w') as f:
        f.write(f"<h1>Welcome to the {app_name} app!</h1>\n")
        
    print(f"App '{app_name}' created successfully with extra structure.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python create_app_structure.py <app_name>")
        sys.exit(1)
        
    create_app_structure(sys.argv[1])
