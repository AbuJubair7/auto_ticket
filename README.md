# Bangladesh Railway Auto Ticket Booker

This is a Node.js script designed to automate the process of searching for and booking train tickets on the Bangladesh Railway e-ticketing website. It runs in the terminal and handles the entire process up to the final payment step.

### Features

- Logs in automatically.
- Searches for trains based on route, date, and class.
- Filters for a **specific train** if a name is provided.
- Finds the first train with the required number of available seats.
- Filters for **preferred coaches** and **seat numbers**.
- Reserves seats and handles the OTP verification step.
- Collects passenger details interactively.
- Provides a final review of the booking details before confirming.
- Automatically opens the final payment link in your default browser.

### Setup Instructions

1.  **Install Dependencies:**
    Make sure you have Node.js installed. Then, open your terminal in the project folder and run:

    ```bash
    npm install
    ```

2.  **Create Configuration File:**
    Create a file named `.env` in the root of the project folder. This is where you will store your login credentials and journey details.

### Configuration (`.env` file)

Copy the content below into your `.env` file and fill in your details.

```env
# --- Required Settings ---
MOBILE=YOUR_RAILWAY_MOBILE_NUMBER
PASSWORD=YOUR_RAILWAY_PASSWORD

# --- Journey Details ---
FROM_CITY=Dhaka
TO_CITY=Kurigram
DATE_OF_JOURNEY=25-Sep-2025
SEAT_CLASS=S_CHAIR
NEED_SEATS=2

# --- Optional Filters (Leave blank to ignore) ---
# Example: TRAIN_NAME=KURIGRAM EXPRESS (797)
TRAIN_NAME=

# Example: PREFERRED_COACHES=CHA,JA (use a comma to separate multiple coaches, no spaces)
PREFERRED_COACHES=

# Example: PREFERRED_SEATS=31,32 (use a comma to separate multiple seat numbers, no spaces)
PREFERRED_SEATS=
```

### How to Run

1.  Make sure your `.env` file is saved with your desired trip details.
2.  Run the script from your terminal:
    ```bash
    node rail_booking.js
    ```
3.  The script will start and prompt you for input (like the OTP and other passenger details) when needed.
4.  After you confirm the booking details, it will automatically open the payment link in your browser to complete the purchase.
