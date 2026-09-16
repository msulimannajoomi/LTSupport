import sys

import db
import plan


def _usage():
    print("Usage:")
    print("  python manage.py upgrade <org_id> <account_type>   (trial|prepaid|postpaid)")
    print("  python manage.py topup <org_id> <usd>              (add to a prepaid balance)")
    print("  python manage.py block <org_id>                    (disallow ALL sessions, including interview)")
    print("  python manage.py unblock <org_id>")
    print("Example: python manage.py upgrade ACMECORP-4F2A1B prepaid")
    print("Example: python manage.py topup ACMECORP-4F2A1B 20")
    print("Example: python manage.py block ACMECORP-4F2A1B")


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("block", "unblock"):
        cmd, org_id = sys.argv[1], sys.argv[2]
        db.init_db()
        user = db.get_user_by_org_id(org_id.strip().upper())
        if not user:
            print(f"No account found with org id '{org_id}'.")
            return
        db.set_blocked(user["org_id"], cmd == "block")
        print(f"{user['org_id']} ({user['org_name']}) is now {'BLOCKED' if cmd == 'block' else 'unblocked'}.")
        return

    if len(sys.argv) != 4:
        _usage()
        return

    _, cmd, org_id, value = sys.argv
    db.init_db()
    user = db.get_user_by_org_id(org_id.strip().upper())
    if not user:
        print(f"No account found with org id '{org_id}'.")
        return

    if cmd == "upgrade":
        # A typo here (e.g. "trail" instead of "trial") used to get written straight
        # to the database with no complaint -- and since plan.py only ever matched the
        # exact strings "trial"/"prepaid", any other value silently behaved as
        # unrestricted/postpaid instead of failing loudly. Reject it here instead.
        if value not in plan.ACCOUNT_TYPES:
            print(f"'{value}' isn't a valid account_type -- must be one of: {', '.join(plan.ACCOUNT_TYPES)}.")
            return
        db.set_account_type(user["org_id"], value)
        print(f"Updated {user['org_id']} ({user['org_name']}) to account_type='{value}'.")
    elif cmd == "topup":
        # Entered in dollars for a human operator's sake, stored in cents (see
        # config.PREPAID_RATE_PER_HOUR_CENTS -- the whole prepaid balance is
        # USD-cents-denominated now).
        db.add_balance(user["org_id"], round(float(value) * 100))
        print(f"Added ${value} to {user['org_id']} ({user['org_name']})'s prepaid balance.")
    else:
        _usage()


if __name__ == "__main__":
    main()
