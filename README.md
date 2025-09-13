# Dukaan Dost

**Dukaan Dost** is a smart and automated solution for small kirana stores in India to simplify sales, inventory, and order management via WhatsApp. It helps shopkeepers manage orders, apply offers, generate bills, and get insights on stock and profits — all with minimal effort.

---

## Problem Statement
Small kirana stores rely on manual registers, phone calls, and handwritten notes to manage sales, inventory, and orders. This leads to:
- Errors in billing and stock tracking
- Missed customer orders
- Lack of insights on sales trends and profits

**Dukaan Dost** automates these processes, working with tools shopkeepers already use, like WhatsApp.

---

## Features
- WhatsApp-based order management
- **Planned:** Automatic application of offers (idea under development)
- Itemized billing and total calculation
- Inventory tracking and alerts
- **Planned:** Payment handling via cash and UPI (idea under development)
- Easy to use with minimal setup

---

## Technology Stack
- Python
- Pandas
- WhatsApp API (for automated messages)
- OpenAI API (for advanced automation if integrated)

---

## Installation
1. Clone the repository:
```bash
git clone https://github.com/krishmunjal01/Dukaan-Dost.git
cd Dukaan-Dost

Install dependencies:
pip install -r requirements.txt

Set up your user folder:
Add students.csv, offers.csv, and any related files in users/<username>/

Usage:
1. Start the app:
python app.py
2. Follow WhatsApp prompts to place orders, apply offers, and confirm them.
3. View the final bill with discounts applied.

Note: Payment integration (cash/UPI) is not yet implemented and will be added in future updates.

File Structure
Dukaan-Dost/
├── app.py                  # Main application file
├── requirements.txt        # Dependencies
├── offers.csv              # Active offers
├── users/
│   └── <username>/
│       ├── students.csv
│       ├── students_updated.csv
│       └── test_images/
├── README.md               # Project documentation

Contributing:
1. Fork the repository
2. Create a new branch (git checkout -b feature-name)
3. Make changes and commit (git commit -m "Add feature")
4. Push to branch (git push origin feature-name)
5. Open a Pull Request

License
MIT License

Contact
Krish Munjal – GitHub
