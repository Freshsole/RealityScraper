# Stripe integration – remaining steps

The existing Checkout Session call in [app/billing.py](app/billing.py) (`create_checkout`) was updated in place. No new files, routes or endpoints were added.

## Values to replace

None. `mode`, `success_url`, `cancel_url` and `line_items` already hold real values (subscription mode, `PUBLIC_BASE_URL` based URLs, prices resolved by lookup key `realitify_start_month` / `realitify_pro_month`).

## Configured parameters

**Files containing these parameters:**
- [app/billing.py](app/billing.py)

| Parameter | Value |
|-----------|-------|
| ui_mode | hosted_page |
| billing_address_collection | auto |
| phone_number_collection | {"enabled": true} |
| automatic_tax | {"enabled": false} |
| allow_promotion_codes | true (only when no promo code is pre-applied, see notes) |
| payment_method_collection | always |
| submit_type | auto |
| saved_payment_method_options | {"payment_method_save": "enabled"} |
| integration_identifier | hosted_web_0001 |
| origin_context | web |

## Notes

- `ui_mode`: the installed SDK is stripe 15.6.1 (< 21.0.0), which would normally use `hosted`. The Stripe API of the connected account rejects `hosted` ("no longer supported"), so `hosted_page` is used. Re-check if the SDK or account API version changes.
- `allow_promotion_codes` is sent only when no `discounts` are set. Stripe does not allow both, and the app pre-applies a code when the user entered one.
- `managed_payments` was removed (not part of the Studio configuration). `locale: cs`, `customer`, `metadata` and `subscription_data` are application logic and were kept.

## Setup

Environment variables (see `.env.example`): `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `PUBLIC_BASE_URL`.

`python scripts/stripe_migrate.py setup` creates the products/prices, billing portal configuration and the webhook endpoint (`/api/billing/webhook`) in whichever account the secret key belongs to, so switching to live keys only needs the three variables plus this command.

## How it works

1. `POST` to the billing checkout route calls `create_checkout` and returns the hosted Checkout URL.
2. Stripe redirects back to `/nastaveni/predplatne?billing=success&session_id=...`.
3. The `checkout.session.completed` and `customer.subscription.*` webhooks update the user's billing record.

## Testing

Use Stripe test mode with card `4242 4242 4242 4242`, any future expiry and any CVC. More cards: https://docs.stripe.com/testing

## Resources

https://support.stripe.com and https://docs.stripe.com/mcp
