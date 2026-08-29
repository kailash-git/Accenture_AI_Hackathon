import re
import sqlite3
import pandas as pd

# Small, generic polarity lexicon -- deterministic and free (no LLM call per
# review), per the earlier cost/latency discussion. Not tuned to this
# dataset's specific templates; general everyday sentiment words.
POSITIVE_WORDS = {
    'great', 'good', 'love', 'loved', 'excellent', 'fresh', 'solid',
    'consistent', 'fine', 'happy', 'satisfied', 'nice', 'best', 'enjoy',
    'affordable', 'favorite', 'well', 'stocked', 'recommend', 'resolved',
    'confirms', 'normal', 'time',
}
NEGATIVE_WORDS = {
    'disappointed', 'disappointing', 'empty', 'frustrating', 'bad', 'worst',
    'bug', 'warning', 'overcharging', 'overcharged', 'double', 'complain',
    'complaints', 'damaged', 'inconsistent', 'wish', 'expensive', 'never',
    'cannot', "can't", 'long', 'hard', 'misprint', 'delay', 'delayed',
    'delays', 'stockout', 'shortage', 'refunds', 'demanding', 'empty',
    'critical', 'unknown',
}

_word_re = re.compile(r"[a-z']+")


def score_text(text):
    """Net polarity: count of positive lexicon hits minus negative hits."""
    words = _word_re.findall(text.lower())
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    return pos - neg


def get_feedback_monthly_sentiment(db_path):
    """
    Scores every unstructured_feedback row with score_text(), then aggregates
    to item x state x month bins (mean sentiment, review count).

    Only bins with at least one real review are included -- no zero-filled
    gap months. Fabricating a 'neutral' sentiment reading for a month with no
    actual review would not be real evidence, and with ~100 reviews spread
    across 3 items x 2 states x 24 months, most months have zero reviews.
    """
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM unstructured_feedback", conn)
    conn.close()

    df['sentiment'] = df['text_content'].apply(score_text)
    df['date'] = pd.to_datetime(df['date'])
    df['month'] = df['date'].dt.to_period('M').astype(str)

    agg = df.groupby(['item_id', 'state_id', 'month']).agg(
        mean_sentiment=('sentiment', 'mean'),
        review_count=('sentiment', 'size'),
    ).reset_index()
    agg['month_date'] = pd.to_datetime(agg['month'])
    return agg.sort_values(['item_id', 'state_id', 'month_date']).reset_index(drop=True)
