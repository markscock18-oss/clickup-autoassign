import os
import re
import time

import requests

API = "https://api.clickup.com/api/v2"
API_TOKEN = os.environ["CLICKUP_TOKEN"]
LIST_ID = os.getenv("CLICKUP_LIST_ID", "901525004358")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
RUN_SECONDS = float(os.getenv("RUN_SECONDS", "240"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
# New orders start in this status; while you have one still in it, don't claim another
BUSY_STATUS = os.getenv("BUSY_STATUS", "new order").lower()
# Only orders created within this many seconds count as new
NEW_ORDER_MAX_AGE = float(os.getenv("NEW_ORDER_MAX_AGE", "180"))
# Remembered between runs: every order the bot gave you, and the highest order number it has seen
CLAIMED_FILE = os.getenv("CLAIMED_FILE", "claimed_ids.txt")
SERIAL_FILE = os.getenv("SERIAL_FILE", "last_serial.txt")

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


def serial(task):
    """Order number from a name like "#3054", or None if it isn't an order."""
    if task.get("parent"):
        return None  # subtasks are never orders
    match = re.match(r"\s*#\s*(\d+)", task.get("name", ""))
    return int(match.group(1)) if match else None


def status(task):
    return (task.get("status") or {}).get("status", "").lower()


def load_claimed():
    try:
        with open(CLAIMED_FILE) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def remember_claimed(task_id, claimed):
    claimed.add(task_id)
    with open(CLAIMED_FILE, "a") as f:
        f.write(task_id + "\n")


def save_serial(number):
    with open(SERIAL_FILE, "w") as f:
        f.write(str(number))


def get_my_id():
    """The token belongs to you, so the token owner is the assignee."""
    user = call("GET", "/user")["user"]
    print(f"[+] Authenticated as {user['username']} (ID: {user['id']})")
    return user["id"]


def list_tasks(**params):
    """All top-level tasks in the list matching params, following pagination."""
    page = 0
    while True:
        data = call("GET", f"/list/{LIST_ID}/task", params={"page": page, "subtasks": "false", **params})
        yield from data.get("tasks", [])
        if data.get("last_page", True):
            return
        page += 1


def starting_serial():
    """Highest order number already in the list (older than the "new" window), or remembered from before."""
    try:
        with open(SERIAL_FILE) as f:
            remembered = int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        remembered = 0
    cutoff = (time.time() - NEW_ORDER_MAX_AGE) * 1000
    in_list = [
        serial(t) or 0
        for t in list_tasks(include_closed="true")
        if int(t.get("date_created") or 0) <= cutoff
    ]
    return max([remembered, *in_list])


def next_new_order(state):
    """Return the next brand-new unassigned order to claim, or None.

    An order only counts as new if its number is higher than every order seen before,
    it was created in the last few minutes, and it is still in "new order".
    Old orders that come back unassigned (e.g. moved back from another list) have a lower number and are skipped.
    """
    created_after = int((time.time() - NEW_ORDER_MAX_AGE) * 1000)
    recent = [t for t in list_tasks(include_closed="false", date_created_gt=created_after) if serial(t)]
    for task in sorted(recent, key=serial):
        number = serial(task)
        if number <= state["last_serial"]:
            continue  # old or already handled
        if task.get("assignees") or task["id"] in state["claimed"]:
            state["last_serial"] = number  # someone else took it (or you were removed): never claim it later
            save_serial(number)
            continue
        if status(task) == BUSY_STATUS and int(task.get("date_created") or 0) > created_after:
            return task
    return None


def current_open_order(my_id):
    """Return your order that is still in "new order", or None if you're free."""
    for task in list_tasks(include_closed="false", **{"assignees[]": my_id}):
        if serial(task) and status(task) == BUSY_STATUS:
            return task
    return None


def main():
    while True:
        try:
            my_id = get_my_id()
            last_serial = starting_serial()
            break
        except requests.RequestException as e:
            print(f"[-] Startup error, retrying: {e}", flush=True)
            time.sleep(5)
    state = {"claimed": load_claimed(), "last_serial": last_serial}
    save_serial(state["last_serial"])
    print(f"[+] Only claiming orders after #{state['last_serial']}", flush=True)
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
                task = next_new_order(state)
                if task and current_open_order(my_id):
                    busy = True  # you picked up an order yourself in the meantime
                elif task and DRY_RUN:
                    print(f"[DRY RUN] Would assign '{task['name']}' ({task['id']})", flush=True)
                elif task:
                    call("PUT", f"/task/{task['id']}", json={"assignees": {"add": [my_id]}})
                    remember_claimed(task["id"], state["claimed"])
                    state["last_serial"] = serial(task)
                    save_serial(state["last_serial"])
                    print(f"[SUCCESS] Assigned '{task['name']}' ({task['id']})", flush=True)
                    busy = True  # the order you just got starts in "new order"
        except requests.RequestException as e:
            print(f"[-] Error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
