# NoSQL-EvoTrace: Demonstration Script & Guided Walkthrough

**Estimated Duration:** 5–8 minutes  
**Target Audience:** Academic evaluators, CSE examiners, and database researchers  
**Prerequisites:** Python 3.11+ installed; dependencies installed via `pip install -r requirements.txt`.

---

## Step 1: Launch Application & Inspect System Status (0:00 - 0:45)
1. In your terminal, start the web server:
   ```bash
   python run.py
   ```
2. Observe the startup banner confirming database configuration, connection URI, and local URL (`http://127.0.0.1:5000/`).
3. Open your browser and navigate to `http://127.0.0.1:5000/`.
4. **Observable Result:**
   - The top navigation bar displays the active database engine badge (e.g. `Mock DB Active` or `MongoDB Connected`).
   - The Overview dashboard shows system metrics: Document Count, Active Schema Fingerprint, Operations, and Registered Queries.

---

## Step 2: Seed Demonstration Dataset (0:45 - 1:30)
1. On the Overview page, click the **🌱 Seed Demo Dataset** button (or run `python cli.py seed`).
2. Confirm the prompt when asked.
3. **Observable Result:**
   - A green banner confirms: `Successfully seeded demo collection 'demo_customers' with 10 documents and 5 sample queries.`
   - Collection Documents increments to `10`.
   - A baseline schema snapshot is created with a deterministic fingerprint (e.g. `2facc13f3c044067`).

---

## Step 3: Explore Observed Schema Profile (1:30 - 2:30)
1. Click **Schema Explorer** in the top navigation.
2. Select `demo_customers` from the collection dropdown.
3. **Observable Result:**
   - A comprehensive table displays all 15 observed field paths (e.g., `_id`, `address`, `address.city`, `age_str`, `loyalty_points`, `notes`, `postal_code`, `status`, `tags`).
   - Notice the **Presence Rate** visual bars: `address` is present in 80% of docs; `notes` has a presence rate of 70% with 3 explicit nulls and 3 missing keys.
   - Point out that EvoTrace preserves the distinction between missing optional fields and explicit `null` values.

---

## Step 4: Review Registered Queries & Dependencies (2:30 - 3:15)
1. Click **Query Registry** in the top navigation.
2. Review the 5 registered sample queries:
   - *Active Cambridge Customers* (depends on `status`, `city`)
   - *VIP & High Loyalty Customers* (depends on `loyalty_points`)
   - *Customers with Postal Code* (depends on `postal_code`)
   - *Massachusetts State Address* (depends on `address.state`)
   - *Customers With Tags* (depends on `tags`)
3. Click the **▶ Test** button next to *Customers with Postal Code*.
4. **Observable Result:**
   - A panel shows that 10 documents currently match `{"postal_code": {"$exists": true}}` with sub-millisecond execution latency.

---

## Step 5: Controlled Schema Evolution with Dry-Run Preview (3:15 - 4:45)
1. Click **Evolution Workspace** in the top navigation.
2. Configure the transformation:
   - **Target Collection:** `demo_customers`
   - **Evolution Operation:** `rename_field`
   - **Source Field Path:** `postal_code`
   - **Destination Field Path:** `zip_code`
3. Click **🔍 Preview Dry-Run**.
4. **Observable Result:**
   - The simulation panel opens without modifying the database.
   - Metrics appear: *Total Docs: 10, Will Mutate: 10, Skipped: 0, Conflicts: 0*.
   - A Registered Query Impact warning appears: `1 potentially affected`. The query *Customers with Postal Code* is flagged because its referenced path `postal_code` is being renamed.
   - Diff previews show before/after field states: `[postal_code: '02138'] ➔ [zip_code: '02138']`.

---

## Step 6: Apply Mutation & Inspect Fine-Grained Provenance (4:45 - 5:45)
1. Check the explicit confirmation box:
   `[x] I confirm that I have reviewed the planned transformation...`
2. Click **⚡ Apply Mutation**.
3. **Observable Result:**
   - You are redirected to the **Provenance Details** page for the newly generated operation ID.
   - Shows pre-schema snapshot ID and resulting post-schema snapshot ID.
   - The Document State Transitions table shows each affected document with before and after states and value fingerprints.

---

## Step 7: Safe Recovery & Downstream Conflict Protection (5:45 - 7:00)
1. Click **Recovery Center** in the top navigation.
2. Select the executed `rename_field` operation.
3. Observe the **Recovery Preview**:
   - Confirms *10 restorable documents*, *0 conflicts*.
4. Now, to demonstrate conflict awareness:
   - Open a terminal and simulate a downstream user edit on one document:
     ```bash
     python -c "from evotrace.database import get_db; db = get_db(); db['demo_customers'].update_one({'customer_id': 'CUST-1001'}, {'$set': {'zip_code': '99999'}}); print('Simulated downstream edit on CUST-1001')"
     ```
5. Refresh the Recovery Preview page:
   - **Observable Result:**
     - The recovery engine flags: `Downstream Conflicts Detected (1)`.
     - Document `CUST-1001` is marked as conflicting: *Current fingerprint diverges from recorded post-operation state*.
     - Restorable docs count changes to `9`, with `1 conflict`.
6. Check the confirmation checkbox and click **↩️ Execute Safe Recovery**.
7. **Observable Result:**
   - 9 compatible documents are restored back to `postal_code`.
   - The edited document (`CUST-1001`) was safely skipped and retains its user-edited `zip_code: '99999'` without silent data destruction!
   - A new recovery audit record is logged.

---

## Step 8: Research Evaluation Suite (7:00 - 8:00)
1. Click **Experiments** in the top navigation.
2. In the benchmark form, specify dataset sizes `100,500` and seed `42`.
3. Click **▶ Run Evaluation Suite**.
4. **Observable Result:**
   - The suite runs controlled scenarios against synthetic ground-truth collections.
   - Persisted table populates with empirical metrics: $F_1 = 1.0$, Recovery Correctness $= 1.0$, Operation Latency, and Provenance Overhead (~315 B/doc).
   - Click **📥 Export CSV** to download `nosql_evotrace_benchmark_results.csv`.
