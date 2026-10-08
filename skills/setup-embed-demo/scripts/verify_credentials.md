# Verify Credentials Reference Script

> [!NOTE]
> Execute this script via `lkr code-mode sandbox` or the Looker 4.0 Python SDK to verify active credentials and Admin role (`role_id: 2`).

```python
import sys

log_msgs = []


def log(msg):
    log_msgs.append(msg)
    sys.stderr.write(msg + "\n")


try:
    log("Verifying Looker SDK authentication...")
    user = me()
    log(f"Successfully authenticated as: {user.display_name} (ID: {user.id}, Role ID: {user.role_ids})")

    is_admin = 2 in user.role_ids if user.role_ids else False
    if not is_admin:
        log("Note: User does not appear to hold the standard Admin role (role_id: 2). Verify privileges if needed.")

    return {
        "status": "success",
        "logs": log_msgs,
        "user": {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role_ids": user.role_ids,
            "is_admin": is_admin,
        },
    }
except Exception as e:
    log(f"Authentication verification failed: {e}")
    return {
        "status": "error",
        "logs": log_msgs,
        "error": str(e),
    }
```
