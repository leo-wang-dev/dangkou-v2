# Sheet Category Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build merchant-scoped dynamic category templates where each Excel Sheet defines one category and its header fields, with approval, import, management, template download, and customer-safe display.

**Architecture:** Add a focused dynamic-template repository backed by JSON field definitions and a generic dynamic-product table. Preserve the existing razor/curler physical tables behind the current compatibility paths while new categories use the dynamic repository; expose both through unified catalog responses. Excel discovery is deterministic for workbook structure and image anchors, while semantic defaults remain reviewable in the approval ticket.

**Tech Stack:** Python 3.12, FastAPI, SQLite, openpyxl, vanilla HTML/JS, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-18-sheet-category-template-design.md`

## Global Constraints

- One visible Sheet equals one category candidate.
- Preserve source text and embedded images; never invent a missing model.
- One data row equals one product, including repeated or empty models.
- All template and product writes require the existing approval-token flow.
- TG customer replies expose only a bounded sample of approved, visible products and public non-price fields; they never return a full catalog page link.
- Existing razor/curler data and endpoints remain compatible.

---

### Task 1: Dynamic template and product repository

**Files:**
- Create: `catalog/dynamic_catalog.py`
- Modify: `catalog/schema.sql`
- Modify: `catalog/db.py`
- Test: `tests/test_dynamic_catalog.py`

**Interfaces:**
- Produces `FieldDef`, `TemplateDef`, `list_templates(conn)`, `get_template(conn,key)`, `approve_template(conn,draft)`, `list_products(conn,key,public_only=False)`, and `upsert_approved_products(conn,key,rows)`.

- [x] Write tests proving seed compatibility, field validation, template versioning, blank-model preservation, repeated-model row preservation, and public-field filtering.
- [x] Run the tests and confirm failure because the repository and tables do not exist.
- [x] Add the three tables and implement the minimal repository.
- [x] Run the focused tests, then existing database/template tests.

### Task 2: Workbook discovery and draft creation

**Files:**
- Create: `catalog/workbook_templates.py`
- Modify: `catalog/ingest.py`
- Modify: `catalog/agent.py`
- Test: `tests/test_workbook_templates.py`
- Test: `tests/test_dynamic_import.py`

**Interfaces:**
- Produces `discover_workbook(path) -> list[SheetDraft]`, including header row, fields, rows, image anchors and fingerprints.
- Extends `ingest.start(..., category=None)` to generate one or more template-aware ticket sections.

- [x] Add the supplied blow-dryer workbook as an external test input path guarded by a generated equivalent fixture for CI.
- [x] Write failing tests for row-2 headers, one Sheet/category, 11 columns, seven images, three images on one row, blank models, and repeated models.
- [x] Implement deterministic header detection, safe field keys, field-role defaults, merged-cell handling, image extraction and row fingerprints.
- [x] Write and pass integration tests for first import, existing-template match and uncertain template change.

### Task 3: Atomic template plus product approval

**Files:**
- Modify: `catalog/tickets.py`
- Modify: `catalog/api.py`
- Test: `tests/test_dynamic_import.py`
- Test: `tests/test_crud_api.py`

**Interfaces:**
- Adds ticket payload kind `template_import` and decisions for template metadata plus per-row approval.
- Adds authenticated category/template list, update and template-download endpoints.

- [x] Write failing tests for atomic approval, rejection, token reuse, conflicting template revision, suspicious delist and empty model rows.
- [x] Implement transaction-safe decision handling and template version snapshots.
- [x] Implement standard XLSX template download with title, headers, example row and image column.
- [x] Run import, ticket, authorization and rollback tests.

### Task 4: Merchant H5 and WeChat tools

**Files:**
- Modify: `static/index.html`
- Modify: `engine-plugin/catalog-v2.mjs`
- Test: `tests/e2e/test_dynamic_categories.py`
- Test: `tests/test_plugin_syntax.mjs`

**Interfaces:**
- H5 reads categories from `/categories`, renders template/product fields dynamically, and exposes template creation/download.
- WeChat import accepts an Excel path without a fixed two-value category enum and reports per-Sheet approval results.

- [x] Write failing browser tests for dynamic tabs, field visibility controls, template approval preview and template download.
- [x] Write failing plugin tests for category-optional import and no fixed enum.
- [x] Implement H5 and plugin changes without changing token handling.
- [x] Run focused browser and Node tests.

### Task 5: Unified merchant catalog and bounded TG product replies

**Files:**
- Modify: `catalog/customer_catalog.py`
- Modify: `catalog/csbot.py`
- Modify: `catalog/api.py`
- Modify: `catalog/search.py`
- Test: `tests/test_customer_showcase.py`
- Test: `tests/test_purchase_conversation.py`
- Test: `tests/test_dynamic_search.py`

**Interfaces:**
- Internal customer-catalog reads return both legacy and dynamic products with safe specs only.
- TG category listing, model search and image candidates work for dynamic categories, but broad queries return only a bounded sample in chat with photos and no link or price.

- [x] Write failing tests showing dynamic products in TG replies and proving links, prices and internal cost fields never appear.
- [x] Implement unified reads, bounded text/photo delivery and dynamic image lookup/indexing.
- [x] Run customer, redline, photo and search regression suites.

### Task 6: Real workbook and full-flow verification

**Files:**
- Create: `scripts/verify_dynamic_template_import.py`
- Create: `audit/round20/动态分类模板验收.md`

**Interfaces:**
- Verification script takes an XLSX path and emits a disposable SQLite database, approval result, downloaded template and JSON evidence.

- [x] Run the supplied `成本报价单.xlsx` through discovery, approval and customer display in an isolated database.
- [x] Assert category `吹风机`, source header labels, seven embedded images, blank-model preservation, duplicate-model row preservation and private cost filtering.
- [x] Run the full Python, Node and Playwright suites once focused tests pass.
- [x] Record exact pass counts, remaining deployment constraints and artifact links.
