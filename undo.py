"""One-time undo: remove you from the tasks listed in undo_ids.txt."""
from main import call, get_my_id

my_id = get_my_id()
with open("undo_ids.txt") as f:
    task_ids = [line.strip() for line in f if line.strip()]

for task_id in task_ids:
    try:
        call("PUT", f"/task/{task_id}", json={"assignees": {"rem": [my_id]}})
        print(f"[UNDONE] {task_id}", flush=True)
    except Exception as e:
        print(f"[-] {task_id}: {e}", flush=True)
print(f"[+] Finished {len(task_ids)} tasks")
