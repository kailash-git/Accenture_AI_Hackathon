# BusinessIntelligence.ai Target Persona Profiles
## AIC 2026 — Track 3: KPI Intelligence-to-Action Engine
### Mapped by Sivasubramanian S | Stack Overflowed (IIT Madras)

This document establishes the user profiles, metrics, permissions, and narrative requirements for our two target personas. It will be used directly to construct the dynamic LLM system prompts on Day 5 and the UI column-level security filters on Day 6.

---

## 1. Executive Persona: VP of Retail Sales

### 1.1 Strategic Profile
*   **Operational Goals**:
    *   Maximize overall regional revenue and optimize product gross margin percentage.
    *   Maintain market share across product categories.
    *   Optimize retail pricing strategies and regional marketing spend efficiency.
*   **Decision Rights**:
    *   Authorize regional pricing promotions or adjustments.
    *   Reallocate marketing budgets between regions and product categories.
    *   Initiate strategic supplier renegotiations.
*   **Narrative Requirements**:
    *   **Style**: High-level, action-oriented, executive summary.
    *   **Focus**: Financial impact, root cause categorization (e.g., price vs. volume vs. mix), and macro trends.
    *   **Length**: Under 250 words, using bulleted takeaways.
    *   **Actions Format**: Emphasize gross margin impact, overall ownership, and high-level risk assessment.

### 1.2 Data Access & Entitlements
*   **Granularity View**: Regional, product category, and monthly/weekly aggregates.
*   **Allowed Data Columns**:
    *   `Revenue`, `GrossMarginPercent`, `MarketingSpend`, `AverageSellingPrice`, `SalesVolume`.
    *   Regional aggregation categories (e.g., `Region = West`, `Category = Electronics`).
*   **Restricted Data Columns (Masked/Blocked)**:
    *   Detailed logistics variables: `SKU_ID`, `Warehouse_Bay_Number`, `Carrier_Tracking_ID`, `Average_Lead_Time_Days`.
    *   Sensitive database internals: `Transaction_ID`, `Row_Insertion_Timestamp`.

### 1.3 Sample Narrative Output (July 10 West Region 12% Revenue Anomaly)
> **Executive Summary: West Region Revenue Anomaly**
> 
> **Finding**: On July 10, the West Region experienced a **12.4% drop in Revenue** relative to the seasonal baseline, resulting in a **$45,000 margin variance**.
> 
> **Root Cause**: Contribution analysis isolates this drop to a **negative volume effect ($52,000)** on premium electronics, partially offset by a **positive price effect ($7,000)**. This indicates a **price elasticity response**: a recent 15% price increase on high-volume smart devices caused sales volume to contract by 25%. Customer support tickets confirm rising complaints regarding the pricing change, suggesting price resistance.
> 
> **Recommended Action**:
> *   *Driver*: Pricing-elasticity volume drop.
> *   *Controllable Lever*: Promotional discounts.
> *   *Action*: Apply a targeted 10% promotional discount on smart devices in the West Region for the next 7 days.
> *   *Expected Impact*: Reclaim $35,000 in lost revenue volume.
> *   *Confidence*: High (88%)
> *   *Monitoring Plan*: Track daily sales volume on smart devices in the West Region.

---

## 2. Analyst Persona: Regional Supply Chain Planner

### 2.1 Operational Profile
*   **Operational Goals**:
    *   Maintain optimal inventory turnover ratios and minimize carrying costs.
    *   Eliminate stockouts and inventory shrinkage in warehouses.
    *   Optimize supplier lead times and warehouse bay logistics.
*   **Decision Rights**:
    *   Trigger automated supplier reorder quantities.
    *   Approve inter-warehouse stock transfer requests.
    *   Flag supplier contract lead-time violations.
*   **Narrative Requirements**:
    *   **Style**: Analytical, granular, technical, and logistics-focused.
    *   **Focus**: Specific SKU details, warehouse bay locations, shipping delays, carrier performance, and data freshness metrics.
    *   **Length**: Under 400 words, including tabular data references.
    *   **Actions Format**: Operational instructions showing quantities, locations, SKU identifiers, and inventory thresholds.

### 2.2 Data Access & Entitlements
*   **Granularity View**: SKU-level, warehouse-level, carrier-level, and hourly/daily logs.
*   **Allowed Data Columns**:
    *   `SKU_ID`, `Product_Name`, `Inventory_On_Hand`, `Reorder_Point`, `Warehouse_Bay_Number`, `Lead_Time_Days`, `Carrier_Performance_Score`.
*   **Restricted Data Columns (Masked/Blocked)**:
    *   Financial profit margins: `GrossMarginPercent`, `CostOfGoodsSold`, `RegionalProfitMargin`, `SupplierRawCost`.
    *   Corporate marketing metrics: `MarketingSpend`, `Campaign_ROI`.

### 2.3 Sample Narrative Output (July 10 West Region 12% Revenue Anomaly)
> **Operational Analysis: West Region Inventory Shortage**
> 
> **Finding**: Inventory turnover for **SKU-992 (Smart Hub Pro)** dropped to **1.2** in the Seattle Warehouse (Bay 14) on July 10, causing a regional stockout alert.
> 
> **Root Cause**: Structured inventory data indicates stock-on-hand fell to 0 units against a reorder threshold of 50 units. Supplier shipping logs confirm that *Carrier LogiTrans* delayed delivery of Shipment #88912 by 4 days due to route delays, extending the lead time from 3 to 7 days. Unstructured carrier emails verify carrier delay issues.
> 
> **Recommended Action**:
> *   *Driver*: Supplier shipping delay causing regional stockout.
> *   *Controllable Lever*: Warehouse safety stock limits.
> *   *Action*: Trigger emergency transfer of 150 units of SKU-992 from the Portland Warehouse, and increase the Seattle safety stock threshold by 15 units.
> *   *Expected Impact*: Resolve local stockout in 24 hours.
> *   *Confidence*: Very High (94%)
> *   *Monitoring Plan*: Monitor shipment updates from LogiTrans.

---

## 3. Entitlement & UI Masking Mapping Matrix

This matrix governs the column-level visibility rules implemented in our UI Dashboard database queries:

| Database Column | VP of Retail Sales | Regional Supply Chain Planner | Masking Action |
|---|---|---|---|
| `Revenue` | **Visible** | *Blocked* | Redact cell values in planner view |
| `GrossMarginPercent` | **Visible** | *Blocked* | Redact cell values in planner view |
| `CostOfGoodsSold` | **Visible** | *Blocked* | Redact cell values in planner view |
| `SKU_ID` | *Blocked* | **Visible** | Show as "RESTRICTED" in VP view |
| `Warehouse_Bay_Number` | *Blocked* | **Visible** | Show as "RESTRICTED" in VP view |
| `Carrier_Performance` | *Blocked* | **Visible** | Show as "RESTRICTED" in VP view |
| `Inventory_On_Hand` | **Visible** | **Visible** | No restriction |
| `SalesVolume` | **Visible** | **Visible** | No restriction |

---

## 4. Prompt Customization Rules (For Day 5 System Prompts)

To ensure the LLM generates the appropriate narrative, the pipeline must dynamically swap the system prompt instruction based on the active header role:

```python
def get_system_prompt(persona: str) -> str:
    base_instructions = "You are a KPI Storytelling Engine. Do not hallucinate quantitative facts. Refer to the data tables provided."
    if persona == "vp_sales":
        return base_instructions + (
            "\nFormat the output for an Executive VP of Sales. "
            "Write a concise summary (<250 words) focusing on macro financial impact, "
            "Price-Volume-Mix drivers, and high-level decisions. Do not show SKU IDs, "
            "warehouse bay numbers, or logistics metrics."
        )
    elif persona == "supply_planner":
        return base_instructions + (
            "\nFormat the output for a Regional Supply Chain Planner. "
            "Write a technical, detailed log (<400 words) focusing on SKU numbers, "
            "warehouse names, shipping delays, and inventory reorder metrics. "
            "Do not show profit margins, COGS, or financial revenue figures."
        )
    else:
        raise ValueError("Invalid Persona")
```
