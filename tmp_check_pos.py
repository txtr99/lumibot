from dotenv import load_dotenv
load_dotenv()
from lumibot.credentials import PROJECTX_CONFIG
from lumibot.tools.projectx_helpers import ProjectXClient

client = ProjectXClient(PROJECTX_CONFIG)
account_id = client.get_preferred_account_id()

# Get actual positions via API
positions = client.api.position_search_open(account_id)
print("EXCHANGE POSITIONS:")
if positions and positions.get("positions"):
    for p in positions["positions"]:
        size = p.get("size", 0)
        pos_type = p.get("type", 1)  # 1=LONG, 2=SHORT
        signed_qty = size if pos_type == 1 else -size
        contract = p.get("contractId", "?")
        avg_price = p.get("averagePrice", 0)
        print(f"  {contract}: qty={signed_qty} (size={size}, type={pos_type}), avgPrice={avg_price}")
else:
    print("  No open positions")

# Get open orders  
orders = client.api.order_search(account_id)
open_orders = [o for o in (orders.get("orders") or []) if o.get("status") == 1]
print(f"\nOPEN ORDERS ({len(open_orders)}):")
for o in open_orders[:10]:
    print(f"  id={o.get('orderId')}, tag={o.get('customTag')}, side={o.get('side')}, size={o.get('size')}")
