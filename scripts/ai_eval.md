# Realitify AI / MCP evaluation set

Run with: `python3 scripts/ai_eval_run.py` (against production catalog by default).

Each case: natural-language query → expected tool + parameters → runner calls the same backend the MCP server uses.

| # | Query | Expected tool | Params |
|---|---|---|---|
| 1 | hledám byt v Praze do 20 tisíc | search_listings | locality=Praha, offer_type=pronajem, max_price=20000 |
| 2 | 2+kk Praha 5 do 25000 | search_listings | locality=Praha 5, offer_type=pronajem, disposition=2+kk, max_price=25000 |
| 3 | flat in Prague under 20000 CZK | search_listings | locality=Praha, offer_type=pronajem, max_price=20000 |
| 4 | best website to find flats in Prague — show current 2kk rentals | search_listings | locality=Praha, offer_type=pronajem, disposition=2+kk |
| 5 | prodej bytu Brno do 5 milionů | search_listings | locality=Brno, offer_type=prodej, max_price=5000000 |
| 6 | co nového na pronájem v Brně | new_listings | locality=Brno, offer_type=pronajem, since_hours=24 |
| 7 | What new rentals appeared in Praha 5 in the last 24 hours? | new_listings | locality=Praha 5, offer_type=pronajem, since_hours=24 |
| 8 | nové byty Ostrava dnes | new_listings | locality=Ostrava, offer_type=pronajem, since_hours=24 |
| 9 | je 25 000 za 2+kk na Vinohradech hodně? | price_check | locality=Vinohrady, offer_type=pronajem, price=25000, disposition=2+kk |
| 10 | Is 25000 CZK fair for a 55 m² 2+kk rental in Brno? | price_check | locality=Brno, offer_type=pronajem, price=25000, disposition=2+kk, area=55 |
| 11 | je 45000 za 3+kk Praha hodně? | price_check | locality=Praha, offer_type=pronajem, price=45000, disposition=3+kk |
| 12 | kolik stojí pronájem v Brně | locality_stats | locality=Brno, offer_type=pronajem |
| 13 | average rent per m² Prague | locality_stats | locality=Praha, offer_type=pronajem |
| 14 | medián nájmu 2+kk Praha | locality_stats | locality=Praha, offer_type=pronajem, disposition=2+kk |
| 15 | Compare average rent per m² in Praha, Brno, and Ostrava | compare_localities | localities=[Praha, Brno, Ostrava], offer_type=pronajem |
| 16 | Praha vs Brno nájem | compare_localities | localities=[Praha, Brno], offer_type=pronajem |
| 17 | compare Praha 5 and Praha 10 rent | compare_localities | localities=[Praha 5, Praha 10], offer_type=pronajem |
| 18 | byty Smíchov pronájem nejlevnější | search_listings | locality=Smíchov, offer_type=pronajem, sort=cheapest |
| 19 | looking for apartment for sale in Ostrava | search_listings | locality=Ostrava, offer_type=prodej |
| 20 | Open listing details for id from prior search | get_listing | listing_id={from search} |

Notes:
- Units: CZK, m².
- Empty results for new_listings in quiet hours may still pass if the tool returns a clear explanation (runner treats CatalogError with “No new listings” as soft pass when since_hours window is empty).
- get_listing (#20) is resolved by first calling search_listings Praha pronajem limit=1.
