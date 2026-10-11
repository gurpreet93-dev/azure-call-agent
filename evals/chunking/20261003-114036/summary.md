# Chunking experiment 20261003-114036

Backend **bm25** · top-3 · 32 questions · documents: summit-company-info.txt, summit-customer-handbook-DEMO.docx, summit-price-list-DEMO.xlsx

| Strategy | recall@3 | MRR | Chunks | Avg chunk chars | Avg chars retrieved/question |
|---|---|---|---|---|---|
| fixed-500 (baseline) | 75% | 0.65 | 29 | 482 | 1,348 |
| fixed-500 + overlap-100 | 75% | 0.67 | 36 | 480 | 1,317 |
| **sentence-600** | 81% | 0.72 | 27 | 517 | 1,417 |
| sentence-600 + overlap-150 | 75% | 0.70 | 32 | 528 | 1,493 |
| recursive-600 | 75% | 0.67 | 30 | 465 | 1,293 |
| headings-300 + path | 75% | 0.66 | 64 | 266 | 757 |
| headings-800 (no label) | 78% | 0.65 | 37 | 350 | 1,018 |
| headings-800 + path | 78% | 0.65 | 37 | 416 | 1,177 |
| headings-1500 + path | 78% | 0.65 | 36 | 426 | 1,253 |

Best by recall, then MRR, then least text retrieved: **sentence-600** (`sentence-600-ov0-none`).

## Question × strategy (rank of first correct chunk; ❌ = not in top k)

| Question | fixed-500 (baseline) | fixed-500 + overlap-100 | sentence-600 | sentence-600 + overlap-150 | recursive-600 | headings-300 + path | headings-800 (no label) | headings-800 + path | headings-1500 + path |
|---|---|---|---|---|---|---|---|---|---|
| Are you open on Saturdays? | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| How much do you charge to replace gutters? | 2 | ❌ | 1 | 1 | 2 | ❌ | 3 | ❌ | ❌ |
| What does an inspection cost? | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Do you install Timberline shingles? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Water is dripping from my ceiling, what do you need from me? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| How long does a roof inspection take? | 3 | 2 | ❌ | ❌ | ❌ | 2 | 2 | 2 | 2 |
| Can you give me a price for a metal roof? | 1 | 2 | 2 | 1 | 2 | 1 | 1 | 1 | 1 |
| Do you work on flat roofs for businesses? | 3 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| How far do you travel? | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Is tax included in your prices? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| What does it cost to come out after hours for a leak? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 2 | 2 |
| Do you replace the rubber boot around a plumbing vent? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Do I have to get a permit myself? | ❌ | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Should I move my car before you start? | 2 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| What do I do with my dog while you're working? | 1 | 1 | 1 | 1 | 1 | 1 | 2 | 2 | 2 |
| How soon can you start if I sign in April? | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| What happens if it rains on the day? | ❌ | ❌ | 2 | ❌ | 1 | ❌ | 2 | 2 | 2 |
| Will my roof be left open overnight? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| How many days does a new roof take? | ❌ | ❌ | 1 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Is replacing rotten plywood included in the price? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Will there be nails left in my lawn? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| How long is your warranty on a new roof? | 2 | 2 | 3 | 2 | 2 | 2 | 2 | 2 | 2 |
| Does the warranty cover hail damage? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| I'm selling my house, does the warranty go to the buyer? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| How much deposit do you need? | 1 | 1 | 1 | 1 | 1 | 1 | 2 | 2 | 2 |
| Is there a fee to pay by credit card? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Can you deal with my insurance company for me? | 1 | 1 | 1 | 1 | 1 | 2 | 1 | 1 | 1 |
| Can you just put new shingles over the old ones? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Do I need to be home while you work? | 1 | 1 | 2 | 1 | ❌ | 1 | ❌ | 1 | 1 |
| Can you put in a new skylight? | 2 | 2 | 1 | 2 | 2 | 3 | 2 | 2 | 2 |
| Do you work when it's freezing outside? | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| What if the colour I pick is out of stock? | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |

Open `chunks/<strategy>.md` to read every chunk and `retrieved/<strategy>.md` to see what each question pulled back.