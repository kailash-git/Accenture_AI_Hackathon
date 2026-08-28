/* ==========================================================================
   STATE & DATA STORE — Real Injected Anomalies & RBAC Contract
   ========================================================================== */

function _loadFeedbackVotes() {
  try {
    return JSON.parse(localStorage.getItem('bi_feedbackVotes') || '{}');
  } catch (e) {
    return {};
  }
}

const APP_STATE = {
  activeRole: 'vp_sales', // 'vp_sales', 'supply_planner', or 'admin' (full-access governance role)
  activeAnomalyKey: 'supply',
  activeKPI: 'revenue',
  activeTimeRange: 'all',
  activeTab: 'overview',
  isDrawerOpen: false,
  openPvmFactor: null,
  // One vote per anomaly per browser -- { [scenarioKey]: rating }. Without this,
  // clicking the thumbs-up/down repeatedly on the same anomaly inserted a new
  // user_feedback row every time, inflating the Telemetry panel's feedback count
  // with duplicate votes from a single click-happy session rather than reflecting
  // one opinion per anomaly. Persisted to localStorage (_loadFeedbackVotes below)
  // so a page refresh restores prior votes instead of silently forgetting them
  // and allowing the same anomaly to be voted on again.
  feedbackVotes: _loadFeedbackVotes()
};

// OFFLINE FALLBACK DATA ONLY. When api_server.py is reachable, every value shown in the
// UI is overwritten with live, server-computed data from SQLite (see js/api.js
// normalizeAnomalyForUI + app.js loadAnomalyListFromBackend/selectScenario). This static
// object exists solely so the dashboard still renders something if the backend is down;
// it is never the source of truth during normal/judged operation and is visibly flagged
// via the "Offline Demo Data" backend status indicator (see updateConnectionStatusUI).
const ANOMALY_DATASET = {
  supply: {
    id: 'ANOM-2012-11-CA',
    title: 'Supply Constraint',
    category: 'Multi-Factor Variance',
    sku: 'FOODS_3_090',
    item_name: 'Fresh Dairy Grade A (500ml)',
    region: 'CA (West Region)',
    warehouse: 'WH-1000',
    date: 'November 2012',
    zScore: 3.41,
    deviation: '-20.5% fill rate drop',
    confidence: 87,
    status: 'critical',
    badgeText: 'Critical · 87% Conf',
    headline: 'Revenue declined 12.4% in CA',
    summary: 'Warehouse WH-1000 experienced 4 consecutive stockout days with fill rate plunging from 0.98 to 0.78, causing volume contraction of 84% on core SKU.',
    kpi: {
      revenueImpact: '-$5,455',
      unitsLost: '4,320 units',
      fillRate: '0.78',
      stockoutDays: '4 days',
      baselineFillRate: '0.98'
    },
    pvm: {
      volume: { val: -8700, pct: '77%', expl: 'Volume contraction explains 77% of total revenue decline.' },
      price: { val: -3200, pct: '28%', expl: 'Average selling price softened due to promotional mix.' },
      mix: { val: -1900, pct: '17%', expl: 'Unfavorable shift toward lower-margin SKUs.' },
      other: { val: 2100, pct: '19%', expl: 'Positive offset from auxiliary cross-category baskets.' }
    },
    products: [
      { sku: 'FOODS_3_090', volumeDelta: '-84%', revenueImpact: '-$8,204', status: 'Primary Driver' },
      { sku: 'FOODS_3_586', volumeDelta: '-12%', revenueImpact: '-$496', status: 'Secondary' },
      { sku: 'HOUSEHOLD_1_020', volumeDelta: '+4%', revenueImpact: '+$310', status: 'Inelastic' }
    ],
    evidence: [
      {
        id: 'ev-1',
        date: 'Nov 12, 2012',
        source: 'source_supply_monthly',
        type: 'Structured Supply Signal',
        title: 'Fill rate dropped to 0.78 at WH-1000',
        similarity: 0.94,
        similarityTier: 'high',
        preview: 'WH-1000 in CA reported fill_rate=0.78 for FOODS_3_090. Baseline: 0.98. 4 stockout days.',
        fullText: 'Warehouse WH-1000 in California reported fill_rate=0.78 for item FOODS_3_090 in November 2012. Rolling 12-month average fill rate was 0.98. Stockout days count was 4 days. Supplier raw unit cost remained fixed at $0.88. Logistics carrier LogiTrans flagged port congestion delay.'
      },
      {
        id: 'ev-2',
        date: 'Nov 20, 2012',
        source: 'unstructured_feedback (Support Ticket)',
        type: 'Customer Support Escalation #ST-4421',
        title: 'Seattle warehouse arrival cargo delays',
        similarity: 0.91,
        similarityTier: 'high',
        preview: 'Regional manager ticket: "CA distribution partner reporting cargo arrival delays from primary supplier."',
        fullText: 'Support ticket #ST-4421 filed by West regional operations lead: "Our CA distribution partner is reporting significant cargo arrival delays from the primary supplier for FOODS_3_090. Multiple store shelves in the WH-1000 service area are empty. ETA unknown. Request emergency supplier allocation."'
      },
      {
        id: 'ev-3',
        date: 'Nov 22, 2012',
        source: 'unstructured_feedback (Customer Review)',
        type: 'Retail Portal Review #RV-9012',
        title: 'Empty shelf customer review',
        similarity: 0.78,
        similarityTier: 'medium',
        preview: '"Shelves are empty for the third week in a row. Cannot find product anywhere in the area."',
        fullText: 'Verified retail customer review (Rating: 1/5): "Shelves are empty for the third week in a row. Cannot find FOODS_3_090 anywhere in the northern California metro area. Extremely frustrating as this is a staple item." Verified cosine similarity 0.78 to supply disruption cluster.'
      }
    ],
    recommendedAction: {
      title: 'Increase replenishment allocation for FOODS_3_090 in CA',
      expectedImpact: 'Recover approximately $8,204/month in run-rate revenue within 14 days.',
      steps: [
        'Issue emergency PO to secondary supplier (target fill rate ≥ 0.95 within 14 days).',
        'Reallocate 2,000 units buffer stock from Texas warehouse to California WH-1000.',
        'Attach PVM narrative to monthly executive board reporting packet.'
      ]
    },
    synthesis: {
      title: "Supply constraint at primary California distribution partner throttled retail shelf fill rate to 0.78, causing 84% volume loss for SKU FOODS_3_090.",
      body: "The 12.4% regional revenue decline in California was not caused by demand erosion or competitor discounting. Price-Volume-Mix decomposition validates that average sell price held steady at $1.25. Concurrently, 3 independent customer complaints and support ticket #ST-4421 confirm widespread empty shelves across Northern California stores during the exact 4-day stockout window recorded in source_supply_monthly."
    },
    logistics: {
      title: "Supply Logistics & Warehouse Metrics",
      status: "Disrupted",
      statusClass: "critical",
      desc: "Distribution center WH-1000 suffered acute supply throttling due to inbound port shipment delays, causing retail stockouts across 4 major store clusters in Northern California.",
      metrics: [
        { label: "Fill Rate Plunge", val: "0.78", valClass: "danger", sub: "Baseline: 0.98 (-20.5%)" },
        { label: "Stockout Days", val: "4 days", valClass: "danger", sub: "Consecutive outage" },
        { label: "Lead Time Delay", val: "18 days", valClass: "", sub: "+6 days vs SLA" }
      ]
    }
  },

  billing: {
    id: 'ANOM-2013-05-TX',
    title: 'Billing Bug',
    category: 'Conflicting Evidence (Abstain)',
    sku: 'FOODS_3_586',
    item_name: 'Pantry Snack Family Pack',
    region: 'TX (South Region)',
    warehouse: 'WH-2000',
    date: 'May 2013',
    zScore: 1.82,
    deviation: 'Price ×2.0 drift anomaly',
    confidence: 42,
    status: 'warning',
    badgeText: 'Warning · 42% (Abstained)',
    headline: 'Price drift of 2× detected in TX transactions',
    summary: 'Sell price recorded at $3.36 vs expected $1.68 on May 15–16. Customer feedback shows conflicting signals; LLM abstained from automatic margin adjustment.',
    kpi: {
      revenueImpact: '+$4,300 (overcharge)',
      unitsLost: '~1,280 affected',
      fillRate: '1.00',
      stockoutDays: '0 days',
      baselineFillRate: '1.00'
    },
    pvm: {
      volume: { val: -210, pct: '5%', expl: 'Negligible volume elasticity impact.' },
      price: { val: 4300, pct: '92%', expl: 'Artificial revenue inflation from 2x overcharge.' },
      mix: { val: 180, pct: '4%', expl: 'Normal product mix.' },
      other: { val: 90, pct: '2%', expl: 'Minor rounding delta.' }
    },
    products: [
      { sku: 'FOODS_3_586', volumeDelta: '+0%', revenueImpact: '+$4,300', status: 'Pricing Bug' }
    ],
    evidence: [
      {
        id: 'ev-b1',
        date: 'May 15, 2013',
        source: 'fact_sales_daily',
        type: 'Structured Pricing Anomaly',
        title: 'Sell price spike: $1.68 to $3.36 in TX',
        similarity: 0.88,
        similarityTier: 'high',
        preview: 'TX_1 and TX_2 stores recorded sell_price=$3.36 vs contract price $1.68.',
        fullText: 'Fact sales daily records indicate 1,280 transactions for FOODS_3_586 in TX_1 and TX_2 logged at $3.36/unit instead of contract price $1.68. Total overbilling exposure: $2,150. Supplier cost $1.18.'
      },
      {
        id: 'ev-b2',
        date: 'May 18, 2013',
        source: 'unstructured_feedback',
        type: 'Customer Complaint #CC-8812',
        title: 'Double-charge billing complaint',
        similarity: 0.84,
        similarityTier: 'high',
        preview: '"I was charged twice the shelf price on my receipt."',
        fullText: 'Customer complaint filed via POS feedback portal: "Receipt shows $3.36 per unit for snack pack when shelf tag clearly displays $1.68. Overcharged twice this week." Subsequent resolution note marked resolved, triggering low confidence/abstention.'
      }
    ],
    recommendedAction: {
      title: 'Audit TX billing pipeline & issue customer credits',
      expectedImpact: 'Prevent compliance penalty and reconcile $2,150 customer balance.',
      steps: [
        'Audit POS price synchronization batch job for South region.',
        'Issue store credit vouchers to 1,280 impacted loyalty accounts.',
        'LLM abstains from automated KPI baseline shift pending data fix.'
      ]
    },
    synthesis: {
      title: "POS pricing bugs overcharged TX customer accounts by 2x, inflating pricing metrics while triggering support escalations.",
      body: "A pricing synchronization batch failure on May 15 overcharged 1,280 transactions for FOODS_3_586 in TX stores at $3.36 instead of the contract $1.68. While structured database revenue appears inflated, customer complaints confirm billing bugs. The LLM has abstained from automatic baseline adjustments pending manual transaction credits."
    },
    logistics: {
      title: "Pricing Accuracy & Transaction Logs",
      status: "Pricing Bug",
      statusClass: "warning",
      desc: "POS terminal logs indicate pricing drift across multiple checkout registers in the South region, resulting in double-charging anomalies for Snack Packs.",
      metrics: [
        { label: "Price Overcharge", val: "2.0x", valClass: "danger", sub: "$3.36 vs $1.68 expected" },
        { label: "Impacted Tickets", val: "1,280", valClass: "danger", sub: "POS Transactions" },
        { label: "Overcharge Exposure", val: "+$2,150", valClass: "", sub: "Awaiting Credits" }
      ]
    }
  },

  pricecut: {
    id: 'ANOM-2013-08-CA',
    title: 'Price Cut + Volume Lift',
    category: 'Price-Volume-Mix Elasticity',
    sku: 'FOODS_3_090',
    item_name: 'Fresh Dairy Grade A (500ml)',
    region: 'CA (West Region)',
    warehouse: 'WH-1000',
    date: 'August 2013',
    zScore: 2.91,
    deviation: '-25% price, +42% volume',
    confidence: 91,
    status: 'active',
    badgeText: 'Active · 91% Conf',
    headline: 'Promotional price cut drove 42% volume surge',
    summary: 'Deliberate price reduction from $1.67 to $1.25 compressed margin to 30% while expanding market share.',
    kpi: {
      revenueImpact: '+$7,200',
      unitsLost: '+4,200 units lift',
      fillRate: '0.99',
      stockoutDays: '0 days',
      baselineFillRate: '0.98'
    },
    pvm: {
      volume: { val: 7200, pct: '62%', expl: 'Strong demand elasticity response to promotion.' },
      price: { val: -4200, pct: '36%', expl: 'Margin compression from 25% price drop.' },
      mix: { val: 800, pct: '7%', expl: 'Basket cross-selling lift.' },
      other: { val: 300, pct: '3%', expl: 'Seasonal tailwind.' }
    },
    products: [
      { sku: 'FOODS_3_090', volumeDelta: '+42%', revenueImpact: '+$7,200', status: 'Promotional Leader' },
      { sku: 'HOUSEHOLD_1_020', volumeDelta: '+8%', revenueImpact: '+$620', status: 'Basket Attach' }
    ],
    evidence: [
      {
        id: 'ev-p1',
        date: 'Aug 2, 2013',
        source: 'fact_sales_daily',
        type: 'Pricing Strategy Execution',
        title: 'Price reduction from $1.67 to $1.25',
        similarity: 0.95,
        similarityTier: 'high',
        preview: 'Price reduction executed across all CA retail outlets.',
        fullText: 'Sales data confirms deliberate markdown on FOODS_3_090 from $1.67 to $1.25 effective August 2, 2013. Supplier cost remained constant at $0.88. Unit velocity increased 42% week-over-week.'
      }
    ],
    recommendedAction: {
      title: 'Monitor 30-day gross margin compression',
      expectedImpact: 'Sustain volume lift without eroding net operating margin.',
      steps: [
        'Track weekly gross margin threshold (maintain > 28%).',
        'Coordinate with marketing on promotional run duration.',
        'No immediate corrective intervention required.'
      ]
    },
    synthesis: {
      title: "Promotional 25% price reduction in CA successfully expanded market share, driving a 42% unit volume lift.",
      body: "Deliberate markdown from $1.67 to $1.25 on August 2 compressed gross margins to 30% but triggered strong price-elasticity volume gains. PVM decomposition verifies the volume effect offset the margin compression, yielding a positive net revenue impact of $7,200. Cross-selling attachment lifted basket sizes for HOUSEHOLD_1_020."
    },
    logistics: {
      title: "Promotional Elasticity & Volume Lift",
      status: "Active Promo",
      statusClass: "active",
      desc: "Strategic markdown of core retail dairy items in the West region to test demand responsiveness and basket-attach rates for secondary categories.",
      metrics: [
        { label: "Promo Discount", val: "-25%", valClass: "", sub: "$1.25 vs $1.67 baseline" },
        { label: "Volume Surge", val: "+42%", valClass: "", sub: "Unit Velocity Lift" },
        { label: "Basket Attach", val: "+8%", valClass: "", sub: "HOUSEHOLD_1_020 lift" }
      ]
    }
  },

  sparse: {
    id: 'ANOM-2013-08-TX',
    title: 'New Product Launch',
    category: 'Sparse History Variance',
    sku: 'HOUSEHOLD_1_020',
    item_name: 'Premium Household Cleaner (1L)',
    region: 'TX (South Region)',
    warehouse: 'WH-2000',
    date: 'August 2013',
    zScore: 2.12,
    deviation: 'Only 5 days of historical baseline',
    confidence: 95,
    status: 'active',
    badgeText: 'Active · 95% Conf',
    headline: 'New product launch shows high initial sales volume',
    summary: 'Newly launched item HOUSEHOLD_1_020 in TX has only 5 days of historical sales data. Rolling z-score limits are highly sensitive to initial promotional velocity.',
    kpi: {
      revenueImpact: '+$1,850',
      unitsLost: '0 units',
      fillRate: '0.99',
      stockoutDays: '0 days',
      baselineFillRate: '0.98'
    },
    pvm: {
      volume: { val: 1500, pct: '81%', expl: 'High initial trial volume.' },
      price: { val: 200, pct: '11%', expl: 'Standard retail pricing.' },
      mix: { val: 100, pct: '5%', expl: 'Favorable size mix.' },
      other: { val: 50, pct: '3%', expl: 'Minor auxiliary basket.' }
    },
    products: [
      { sku: 'HOUSEHOLD_1_020', volumeDelta: '+420%', revenueImpact: '+$1,850', status: 'New Launch' }
    ],
    evidence: [
      {
        id: 'ev-s1',
        date: 'Aug 10, 2013',
        source: 'fact_sales_daily',
        type: 'Product Lifecycle Registry',
        title: 'SKU launch registration event',
        similarity: 0.98,
        similarityTier: 'high',
        preview: 'HOUSEHOLD_1_020 registered in TX stores on Aug 5, 2013.',
        fullText: 'Product registration details confirm launch date on August 5, 2013. Only 5 sales days recorded. Baseline history is sparse.'
      }
    ],
    recommendedAction: {
      title: 'Establish 30-day baseline before running z-score alerting',
      expectedImpact: 'Prevent false-positive alerts on newly launched items.',
      steps: [
        'Bypass automatic z-score threshold alerts for the first 30 days post-launch.',
        'Track daily velocity manually using warehouse unit logs.',
        'Update semantic contract to flag HOUSEHOLD_1_020 as a new launch.'
      ]
    },
    synthesis: {
      title: "Sparse history for HOUSEHOLD_1_020 triggers z-score sensitivity; baseline setup is in progress.",
      body: "Newly launched items have insufficient historical sales history (only 5 active days recorded since August 5, 2013). Statistical z-score anomaly detectors require a minimum of 21 days to establish a stable rolling average. Consequently, initial sales fluctuations are flagged as anomalies. We recommend bypassing automated z-score alerts for the first 30 days post-launch."
    },
    logistics: {
      title: "Product Launch Logistics & Readiness",
      status: "Launching",
      statusClass: "active",
      desc: "Pre-launch supply pipeline readiness check for premium household cleaners in the South region.",
      metrics: [
        { label: "Active History", val: "5 days", valClass: "", sub: "Launch Date: Aug 5" },
        { label: "Initial Fill", val: "0.99", valClass: "", sub: "WH-2000 readiness" },
        { label: "Trial Velocity", val: "+420%", valClass: "", sub: "Units vs initial forecast" }
      ]
    }
  }
};
