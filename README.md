# Bangladesh Railway Auto Ticket Booker

This is a script designed to automate the process of searching for and booking train tickets on the Bangladesh Railway e-ticketing website. It runs in the terminal and handles the entire process up to the final payment step.

This repository contains two versions of the script: one written in **Node.js** (`rail_booking.js`) and one in **Python** (`rail_booking.py`).

### Features

- Logs in automatically using credentials from a `.env` file.
- Searches for trains based on route, date, and class.
- Can filter for a **specific train** by its name/number.
- Can find the first train that has the required number of available seats.
- Can filter for **preferred coaches** and specific **seat numbers**.
- Reserves seats and handles the OTP verification step.
- Collects other passenger details interactively in the terminal.
- Provides a final review of the booking details before confirming.
- Automatically opens the final payment link in your default browser.

---

## Setup and Usage

You can choose to run either the Node.js or the Python version.

### Option 1: Using the Node.js Script

#### Setup

1.  **Install Dependencies:**
    Make sure you have Node.js installed. Open your terminal in the project folder and run:

    ```bash
    npm install
    ```

2.  **Create Configuration File:**
    Create a file named `.env` in the project folder. This is where you will store your login credentials and journey details.

#### Run the Script

1.  Make sure your `.env` file is saved with your desired trip details.
2.  Run the script from your terminal:
    ```bash
    node rail_booking.js
    ```

---

### Option 2: Using the Python Script

#### Setup

1.  **Prerequisites:**
    Make sure you have Python 3 installed on your system.

2.  **Create a `requirements.txt` file:**
    In the same folder as the script, create a file named `requirements.txt` and add the following lines to it:

    ```
    requests
    python-dotenv
    ```

3.  **Install Dependencies:**
    Open your terminal in the project folder and run:

    ```bash
    pip install -r requirements.txt
    ```

4.  **Create Configuration File:**
    The Python script **requires** a `.env` file to run. Create a file named `.env` in the project folder.

#### Run the Script

1.  Fill in your trip details in the `.env` file.
2.  Run the script from your terminal:
    ```bash
    python rail_booking.py
    ```

---

## Configuration (`.env` file)

Both scripts use the same `.env` file. Copy the content below into your `.env` file and fill in your details.

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
# The Python script also supports a comma-separated list for an exact match: TRAIN_NAME=RANGPUR EXPRESS (771),KURIGRAM EXPRESS (797)
TRAIN_NAME=

# Example: PREFERRED_COACHES=CHA,JA (use a comma to separate multiple coaches)
PREFERRED_COACHES=

# Example: PREFERRED_SEATS=31,32 (use a comma to separate multiple seat numbers)
PREFERRED_SEATS=

# static configurations
DEVICE_ID = 4004028937
REQUEST_TIMEOUT = 20
```
