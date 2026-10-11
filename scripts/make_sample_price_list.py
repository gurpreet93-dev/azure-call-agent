"""Generate sample_kb/summit-price-list-DEMO.xlsx. DEMO PRICES - replace with real ones."""
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = "Approved Price List"
ws.append(["Item", "Unit", "Price (CAD)", "Quotable by phone", "Notes"])
rows = [
    ("Standard roof inspection", "per visit", 199, "Yes", "Credited against any repair or replacement booked within 60 days"),
    ("Emergency leak assessment (business hours)", "per visit", 249, "Yes", "Includes temporary tarp if safe to install"),
    ("Emergency leak assessment (after hours / weekend)", "per visit", 349, "Yes", "Attendance time depends on crew availability"),
    ("Replace missing or damaged shingles (up to 10 shingles)", "per job", 395, "Yes", "Asphalt shingles only; colour match not guaranteed"),
    ("Additional shingles beyond 10", "per shingle", 18, "Yes", "Same visit only"),
    ("Pipe boot / plumbing vent flashing replacement", "each", 185, "Yes", ""),
    ("Seamless aluminum gutter replacement (5 inch)", "per linear foot", 14, "Yes", "Includes removal of old gutters"),
    ("Seamless aluminum gutter replacement (6 inch)", "per linear foot", 17, "Yes", "Includes removal of old gutters"),
    ("Downspout replacement", "each (up to 2 storeys)", 120, "Yes", ""),
    ("Gutter guards", "per linear foot", 9, "Yes", "Only with gutter replacement"),
    ("Asphalt roof replacement - GAF Timberline HDZ", "per square (100 sq ft)", 560, "Indicative only", "Final price after inspection; excludes decking replacement"),
    ("Asphalt roof replacement - CertainTeed Landmark", "per square (100 sq ft)", 545, "Indicative only", "Final price after inspection; excludes decking replacement"),
    ("Standing-seam metal roofing", "per square (100 sq ft)", None, "No", "Inspection required - no phone pricing"),
    ("Commercial / low-slope roofing", "project", None, "No", "Specialist review required"),
]
for r in rows:
    ws.append(list(r))
terms = wb.create_sheet("Terms")
terms.append(["Term", "Detail"])
terms.append(["Sales tax", "13% HST applies to all prices"])
terms.append(["Estimate validity", "Phone estimates are preliminary and valid for 30 days"])
terms.append(["Status", "DEMO PRICE LIST - fictional prices for demonstration"])
wb.save("sample_kb/summit-price-list-DEMO.xlsx")
print("wrote sample_kb/summit-price-list-DEMO.xlsx")
