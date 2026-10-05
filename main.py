import os
import time

import requests

API = "https://api.clickup.com/api/v2"
API_TOKEN = os.environ["CLICKUP_TOKEN"]
LIST_ID = os.getenv("CLICKUP_LIST_ID", "901525004358")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
RUN_SECONDS = float(os.getenv("RUN_SECONDS", "240"))
# Only tasks created after this moment (Unix ms) are claimed, so the old backlog is never touched
CREATED_AFTER_MS = int(os.environ["CREATED_AFTER_MS"])
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
# While you have an order still in this status, don't claim another one
BUSY_STATUS = os.getenv("BUSY_STATUS", "new order").lower()

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
    """Yield new, open, unassigned top-level orders named "#...", following pagination."""
    page = 0
    while True:
        data = call(
            "GET",
            f"/list/{LIST_ID}/task",
            params={
                "page": page,
                "include_closed": "false",
                "subtasks": "false",
                "date_created_gt": CREATED_AFTER_MS,
            },
        )
        for task in data.get("tasks", []):
            if task.get("parent"):
                continue
            if not task.get("name", "").strip().startswith("#"):
                continue  # real orders are named like "#3054"
            if int(task.get("date_created") or 0) <= CREATED_AFTER_MS:
                continue
            if not task.get("assignees"):
                yield task
        if data.get("last_page", True):
            return
        page += 1


def current_open_order(my_id):
    """Return your "#" order that is still in BUSY_STATUS, or None if you're free."""
    data = call(
        "GET",
        f"/list/{LIST_ID}/task",
        params={"assignees[]": my_id, "include_closed": "false", "subtasks": "false"},
    )
    for task in data.get("tasks", []):
        if task.get("parent") or not task.get("name", "").strip().startswith("#"):
            continue
        if (task.get("status") or {}).get("status", "").lower() == BUSY_STATUS:
            return task
    return None


def main():
    my_id = get_my_id()
    print(f"[+] Watching list {LIST_ID} for {RUN_SECONDS:.0f}s (dry run: {DRY_RUN})", flush=True)
    end_time = time.time() + RUN_SECONDS
    busy = True  # check your own orders first
    waiting_on = None
    while time.time() < end_time:
        try:
            # One API call per check: while busy, only watch your open order; while free, only watch for new ones.
            if busy:
                open_order = current_open_order(my_id)
                busy = open_order is not None
                if busy and waiting_on != open_order["id"]:
                    print(f"[WAIT] '{open_order['name']}' is still '{BUSY_STATUS}', not claiming new orders", flush=True)
                    waiting_on = open_order["id"]
                elif not busy and waiting_on:
                    print("[+] Free again, claiming new orders", flush=True)
                    waiting_on = None
            else:
                task = next(unassigned_tasks(), None)
                if task and current_open_order(my_id):
                    busy = True  # you picked up an order yourself in the meantime
                elif task and DRY_RUN:
                    print(f"[DRY RUN] Would assign '{task['name']}' ({task['id']})", flush=True)
                elif task:
                    call("PUT", f"/task/{task['id']}", json={"assignees": {"add": [my_id]}})
                    print(f"[SUCCESS] Assigned '{task['name']}' ({task['id']})", flush=True)
                    busy = True  # the order you just got starts in "new order"
        except requests.RequestException as e:
            print(f"[-] Error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
