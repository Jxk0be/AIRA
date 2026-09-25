# Documents the fake shops uploaded

Not part of any POS. These are the files a shop owner hands us on day one —
the returns policy taped to the counter, the FAQ they answer forty times a
week, the event schedule that lives on a whiteboard.

They exist because half of what a shop knows is not in its sales tables, and
retrieval has to be tested against both halves. Load them with:

    python -m app.rag.ingest --tenant animanga_knox --load sources/documents/animanga_knox
    python -m app.rag.ingest --tenant panel_and_pawn --load sources/documents/panel_and_pawn

Re-running updates each document in place, matched on filename.
