import os
import time

import requests

API = "https://api.clickup.com/api/v2"
API_TOKEN = os.environ["CLICKUP_TOKEN"]
LIST_ID = os.getenv("CLICKUP_LIST_ID", "901525004358")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
RUN_SECONDS = float(os.getenv("RUN_SECONDS", "240"))

session = requests.Session()
session.headers.update({"Authorization": API_TOKEN, "Content-Type": "application/json"})


def call(method, path, **kwargs):
    """Make an API call, waiting out ClickUp's rate limit (429) when hit."""
    while True:
        res = session.request(method, f"{API}{path}", timeout=15, **kwargs)
        if res.status_code != 429:
            res.raise_for_status()
            return res.json()
        reset = float(res.headers.get("X-RateLimit-Reset", time.time() + 10))
        wait = max(1.0, reset - time.time())
        print(f"[!] Rate limited, waiting {wait:.0f}s")
        time.sleep(wait)


def get_my_id():
    """The token belongs to you, so the token owner is the assignee."""
    user = call("GET", "/user")["user"]
    print(f"[+] Authenticated as {user['username']} (ID: {user['id']})")
    return user["id"]


def unassigned_tasks():
    """Yield open, unassigned tasks in the list, following pagination."""
    page = 0
    while True:
        data = call(
            "GET",
            f"/list/{LIST_ID}/task",
            params={"page": page, "include_closed": "false", "subtasks": "true"},
        )
        for task in data.get("tasks", []):
            if not task.get("assignees"):
                yield task
        if data.get("last_page", True):
            return
        page += 1


def main():
    my_id = get_my_id()
    print(f"[+] Watching list {LIST_ID} for {RUN_SECONDS:.0f}s")
    end_time = time.time() + RUN_SECONDS
    while time.time() < end_time:
        try:
            for task in unassigned_tasks():
                call("PUT", f"/task/{task['id']}", json={"assignees": {"add": [my_id]}})
                print(f"[SUCCESS] Assigned '{task['name']}' ({task['id']})")
        except requests.RequestException as e:
            print(f"[-] Error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
