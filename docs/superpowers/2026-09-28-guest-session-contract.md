# Anonymous sessions, batches and photo intent

Technical default: `GUEST_IDLE_SECONDS=86400` (24 hours of inactivity; minimum60 seconds). Authenticated anonymous activity refreshes this window, including list/image/export capabilities. There is no photo, message, export, daily or lifetime normal-use quota.

The central `POST /guest` and shop `POST /cs/chat/{shop_token}/session` issue random capabilities. SQLite keeps only their SHA256 hashes and separate owner IDs. The static and uni-app clients keep anonymous credentials in browser sessionStorage; non-browser app sessions use an in-memory cache. Old localStorage `ut_guest`/`h5v` values are never imported as authorization. Closing a browser cannot reliably signal an immediate server end: a lost session credential has no anonymous recovery path, while the server inactivity deadline still applies.

Central `POST /session/end` revokes the current guest or logged-in bearer token. Shop `POST /cs/chat/{shop_token}/session/end` revokes the supplied `visitor`. Successful end, expiry and merge make the old guest credential unusable. Existing issued-session shop list links check the same session state. On upgrade, pre-existing anonymous list links without an issued-session association are expired; their old note/customer rows are preserved for deliberate migration, but those links and old browser visitor strings cannot recover them. Writes validate before external/model work and again in the final transaction. Model waits hold no live SQLite writer transaction. Cancellation, failure and duplicate retained-photo processing do not leave business rows or unowned newly written photos.

Expired guest notes, batches, customer conversation state and owned photographs are purged opportunistically before/after requests. After a crash or when no traffic arrives, schedule this local maintenance command (for example every15 minutes), once per actual database and photo root:

```sh
python -m catalog.guest_sessions --kind central --db /path/to/userapp.db --photo-dir /path/to/userapp_photos
python -m catalog.guest_sessions --kind shop --db /path/to/catalog.db --photo-dir /path/to/cs_photos
```

The sweep commits row cleanup before unlinking files, rechecks persisted references and confines deletion to the specified photo root. Orphans are eligible after an inactivity window. Expired capability tombstones last at least24 hours beyond expiry, allowing clear410 responses, then the maintenance sweep removes them. Account-owned rows and shared referenced photos survive guest cleanup. Scheduling/deployment of this command belongs to deployment work; no live scheduler was changed here.

## Verified central account merge

Email OTP verification claims only the presented, currently active server-issued central guest capability. Notes, batch/card relationships, current confirmed batch and note photo references move to the verified account in the same transaction; the capability becomes consumed. Replayed/used OTPs cannot repeat the merge. Logout revokes that bearer token and clients start a fresh guest session. Other existing account devices retain their own login tokens.

**Pending product dependency:** whether central account history must include merchant-shop CS notes remains unanswered. No shop database is claimed by a client email, and central login currently claims only data in the central database. This is an incomplete dependent feature, not a central-only history product decision. Shop account binding, cross-shop discovery/history and any authenticated shop-link migration must wait for the explicit choice and trusted server-side account protocol.

## Supplier batches

`note_batches` contains normalized card fields, pending/confirmed/unassigned state, owner, active flag and preset provenance. New photos and text procurement notes reference their collection batch. Card recognition offers a pending switch. Central `/batches/confirm` and shop `/cs/chat/{token}/batches/confirm` select a batch for subsequent notes; optional `note_ids` explicitly attach only unassigned notes owned by that same buyer. Central `/batches` and shop confirmation with `fields` create a manual supplier batch. Re-selecting an existing confirmed batch reuses its Sheet.

A photo containing cards and products leaves products unassigned even if the extractor claims a card-to-product mapping. Multiple cards are separately offered; none silently reassign historic notes. Without a card, a configured bound receiving-shop profile may be captured as an immutable `receiving_shop_preset`; historical notes do not change when that shop later changes its profile. Unknown/legacy notes retain explicit unassigned fallback; the customer-wide legacy latest-card overlay is gone. Explicit per-note supplier edits create a separate manual batch without rewriting the old card or siblings.

Excel exports group by batch ID, with that batch's own card/preset header, literal safe cell values, unique legal worksheet names and note photographs. Different suppliers never merge by display name. Different batch IDs are intentionally kept separate even when their display names match; users may reselect an existing confirmed batch to group later notes. Unresolved batches remain separate. This favors preserving provenance over guessing supplier identity.

## Photo mode and crop contract

The shop session starts with empty `photo_mode`. Initial upload returns `status=intent_required` and retains the file without a model call. `POST /cs/chat/{token}/mode` accepts only `search` or `notes`, applies to subsequent photos and processes the retained photo once. Concurrent retained-photo submissions have one effect. Search does not create procurement notes; notes do not claim catalog identification or quotes. Internal catalog lookup may still establish applicable merchant photo rules. Central photos always use notes mode.

Approved applicable merchant rules alone drive photo handoff classification; there is no platform default rule. A hit writes the merchant notification to `cs_outbox` and the conversation state in the business transaction. An unavailable/invalid classifier fails explicitly rather than inventing a redline.

`图框` is the product rectangle, independently of `_价格框`. Both extraction and review request it. Product coordinates must be finite, increasing, within0–1000. A missing review box inherits an initial box only for an unambiguous unique product name; reorder, duplicate names, dropped products and explicit null/invalid correction cannot copy the wrong rectangle. Multiple-image pixel tests verify real crops. Missing/invalid boxes visibly state that the whole photograph is used.

Both static H5 and uni-app provide current-session controls, visible/changeable mode, card confirmation, manual switch and explicit pending-note association. Task5 owns all13-language resources and final translated phrasing for these controls; this change preserves machine-readable API actions/status codes.
