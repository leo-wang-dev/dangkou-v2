# Business Closure Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify and close the merchant-maintenance-to-customer-note workflow so catalog data can enrich a purchase note while unmatched customer requests still produce a usable Excel file.

**Architecture:** Keep the merchant catalog as the read-only enrichment source and the customer note store as an independent source of truth. Add a deterministic fallback only for explicit single-product purchase statements when the language model returns no valid extraction, then exercise matched, ambiguous, and unmatched catalog paths through the real `CsBot` and XLSX renderer.

**Tech Stack:** Python 3, SQLite, FastAPI, openpyxl, pytest, Playwright, Telegram Bot HTTP API.

**Spec:** `/Users/a1/Downloads/档口智能交易系统-产品总文档-v1.0.md`, `/Users/a1/Downloads/handoff-c-tg-cs-20260918.md`, `/Users/a1/Downloads/C端人工测试清单.md`, plus the product decisions recorded in this conversation after those documents.

## Global Constraints

- The WeChat bot remains the merchant management entry; the merchant-owned Telegram bot remains the customer entry.
- There are no default business redlines. Only merchant-approved rules trigger transfer.
- Customer Telegram responses never expose price, cost, supplier links, or internal catalog fields.
- Catalog matches enrich notes; they do not gate note creation or Excel export.
- A generic category match must not guess a specific product model.
- Customer photos and customer note data must never write back into the merchant product catalog.

---

### Task 1: Requirement and data-flow audit

**Files:**
- Create: `audit/round22/业务闭环综合审计-20260918.md`
- Read: `catalog/csbot.py`, `catalog/purchase_notes.py`, `catalog/customer_catalog.py`, `catalog/dynamic_import.py`, `catalog/dynamic_catalog.py`, `catalog/cs_export.py`

**Interfaces:**
- Consumes: the three supplied documents and later conversation decisions.
- Produces: a traceable requirement matrix with superseded requirements marked explicitly.

- [x] Trace merchant Excel import, approval, product visibility, TG lookup, note capture, catalog attachment, confirmation, and XLSX export.
- [x] Record which old-document requirements were superseded by later product decisions.
- [x] Record every verified gap with a reproducer and its affected boundary.

### Task 2: Deterministic capture for explicit unmatched purchases

**Files:**
- Modify: `catalog/purchase_notes.py`
- Test: `tests/test_purchase_conversation.py`

**Interfaces:**
- Consumes: `capture(bot, cust, text, note_ids=None)` and its model extraction result.
- Produces: an internal fallback returning a validated `create` action containing literal `型号或品名` and `数量` values.

- [x] Add a failing test where the model returns no actions for `我想采购100台吹风机` and the catalog is empty; assert that a draft is created.
- [x] Add negative tests proving `吹风机100台多少钱`, `有没有100台吹风机`, and `如果采购100台吹风机呢` do not create drafts.
- [x] Run the focused tests and confirm the positive test fails for the missing behavior.
- [x] Implement the narrow fallback for explicit purchase verbs plus an explicit quantity; reuse the existing action validation and persistence path.
- [x] Run the focused tests and confirm they pass.

### Task 3: Real catalog enrichment without making catalog membership mandatory

**Files:**
- Modify if required: `catalog/purchase_notes.py`
- Test: `tests/test_purchase_conversation.py`

**Interfaces:**
- Consumes: `customer_catalog.products(conn)` public projections.
- Produces: exact normalized model attachment through `attach(bot, note, product)` while ambiguous category requests remain unbound.

- [x] Add a failing test for an approved, visible dynamic product where `采购100台WX-HD16` links its product id, public specifications, and image.
- [x] Add a test where `采购100台吹风机` has multiple catalog products and remains a valid unbound note.
- [x] Add a test where a completely absent product still becomes a valid unbound note.
- [x] Implement normalized exact matching only if the focused test exposes a gap; do not guess among category candidates.
- [x] Run the focused tests and inspect persisted `fields_json`, source metadata, and photo provenance.

### Task 4: Mixed matched/unmatched Excel export

**Files:**
- Modify if required: `catalog/cs_export.py`
- Test: `tests/test_purchase_conversation.py`

**Interfaces:**
- Consumes: matched and unmatched `cs_note` rows.
- Produces: one workbook containing both rows, dynamic union columns, and only available images.

- [x] Add an integration test that creates one catalog-linked note and one unmatched note, sends `出表`, and opens the returned XLSX.
- [x] Assert both rows exist, the linked row contains catalog fields/image, and the unmatched row retains the customer’s literal model/name and quantity.
- [x] Run the test; change the renderer only if the test exposes a defect.

### Task 5: Merchant maintenance and customer-boundary regression

**Files:**
- Test: `tests/test_dynamic_import.py`, `tests/test_dynamic_catalog.py`, `tests/test_customer_showcase.py`, `tests/test_wechat_capability_matrix.py`, `tests/e2e/test_dynamic_categories.py`
- Create: `audit/round22/业务闭环综合审计-20260918.md`

**Interfaces:**
- Consumes: arbitrary sheet headers, approval tickets, customer visibility, redline rules, and customer catalog projections.
- Produces: evidence for merchant category maintenance and safe customer responses.

- [x] Run dynamic Excel import tests covering one sheet as one category, arbitrary headers, blank models, duplicate models, approvals, updates, and visibility.
- [x] Run customer display tests proving public specifications and images are shown without links, prices, costs, or internal fields.
- [x] Run redline tests proving an empty initial redline and merchant-approved transfer rules.
- [x] Run WeChat capability tests for Excel import, image lookup/onboarding, catalog queries, quote-sheet behavior, shop profile updates, TG token binding, and redline maintenance.

### Task 6: Full verification and server-safe proof

**Files:**
- Modify: `audit/round22/业务闭环综合审计-20260918.md`
- Deploy if changed: `catalog/purchase_notes.py`

**Interfaces:**
- Consumes: local test suite and a copy of the live shop database.
- Produces: repeatable verification evidence and, after local success, the deployed runtime behavior.

- [x] Run the complete Python and Node test suites and record exact pass/fail totals.
- [x] Copy the live database to a temporary path and run matched, category-only, and absent-product note/export scenarios without changing production data.
- [x] Back up changed server files, deploy the verified change, restart only affected services, and inspect health/runtime logs.
- [x] Verify the customer bot can still poll Telegram and the shop API still returns the same shop identity.
- [x] Finish the audit report with closed loops, corrected defects, remaining operational limits, and exact evidence paths.
