import os
import sys
import json

# Setup Django environment
def setup_django():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(project_root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    
    import django
    django.setup()

def execute_sql(query):
    """Executes raw SQL and returns results as JSON."""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        return [
            dict(zip(columns, row))
            for row in cursor.fetchall()
        ]

def execute_orm(code):
    """Executes arbitrary Python code using Django ORM."""
    # This is powerful, use with caution.
    # It expects 'result' variable to be set.
    local_vars = {}
    exec(code, globals(), local_vars)
    return local_vars.get('result')

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python db_query.py <type: sql|orm> <query|code>")
        sys.exit(1)
        
    query_type = sys.argv[1].lower()
    content = sys.argv[2]
    
    setup_django()
    
    try:
        if query_type == 'sql':
            result = execute_sql(content)
        elif query_type == 'orm':
            result = execute_orm(content)
        else:
            print(f"Unknown query type: {query_type}")
            sys.exit(1)
            
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
