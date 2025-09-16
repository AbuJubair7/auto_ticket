#!/usr/bin/env python3
"""
rail_booking.py

Behavior change in this version:
 - DOES NOT use builtin defaults for sensitive/required configuration.
 - It requires specific environment variables to be set (or present in .env).
 - If any required variable is missing or empty the script will exit with an error.

Other behavior:
 - Loads .env (python-dotenv) if present.
 - Parses PREFERRED_COACHES and PREFERRED_SEATS from comma-separated env values.
 - Exact-match train filtering supported (comma-separated).
 - Same booking/conversation flow as before.
"""

import os
import sys
import json
import re
import requests
import webbrowser
from typing import List, Dict, Any

# Attempt to load .env automatically (optional package)
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except Exception:
    # missing python-dotenv is fine — user must provide env vars via shell or other means
    pass

# ---------------- helpers to parse env values ----------------
def parse_list_env(name: str) -> List[str]:
    v = os.getenv(name, "") or ""
    return [s.strip() for s in re.split(r",\s*", v) if s.strip()]

def parse_int_list_env(name: str) -> List[int]:
    parts = parse_list_env(name)
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except Exception:
            continue
    return out

def require_env_vars(keys: List[str]):
    """Ensure required env vars exist and are not empty. Exit with helpful message if not."""
    missing = []
    for k in keys:
        val = os.getenv(k)
        if val is None or str(val).strip() == "":
            missing.append(k)
    if missing:
        print("[rail][FATAL] Missing required environment variables:", ", ".join(missing), file=sys.stderr)
        print("Please create a .env file or export these variables before running.", file=sys.stderr)
        print("Example .env entries:", file=sys.stderr)
        for k in missing:
            print(f"  {k}=<value>", file=sys.stderr)
        sys.exit(1)

# ---------------- CONFIG (read from env; no built-in sensitive defaults) ----------------
# Required keys:
REQUIRED_ENV_KEYS = [
    "MOBILE",
    "PASSWORD",
    "FROM_CITY",
    "TO_CITY",
    "DATE_OF_JOURNEY",
    "SEAT_CLASS",
    "NEED_SEATS",
]

# Validate required env vars early and abort if missing
require_env_vars(REQUIRED_ENV_KEYS)

# At this point required envs exist; build CONFIG using them.
def get_int_env(name: str, fallback: int = None) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        if fallback is None:
            return None
        return int(fallback)
    try:
        return int(v)
    except Exception:
        print(f"[rail][FATAL] Environment variable {name} must be an integer. Got: {v!r}", file=sys.stderr)
        sys.exit(1)

CONFIG = {
    "MOBILE": os.getenv("MOBILE"),
    "PASSWORD": os.getenv("PASSWORD"),
    "FROM_CITY": os.getenv("FROM_CITY"),
    "TO_CITY": os.getenv("TO_CITY"),
    "DATE_OF_JOURNEY": os.getenv("DATE_OF_JOURNEY"),
    "SEAT_CLASS": os.getenv("SEAT_CLASS"),
    "NEED_SEATS": get_int_env("NEED_SEATS"),
    # Optional:
    "TRAIN_NAME": os.getenv("TRAIN_NAME", ""),  # exact match mode (comma-separated allowed)
    "PREFERRED_COACHES": parse_list_env("PREFERRED_COACHES"),
    "PREFERRED_SEATS": parse_int_list_env("PREFERRED_SEATS"),
    "REQUEST_TIMEOUT": get_int_env("REQUEST_TIMEOUT", 20),
    "DEVICE_ID": os.getenv("DEVICE_ID", "4004028937"),
    "REFERER": os.getenv("REFERER", "https://eticket.railway.gov.bd/"),
    "BASE": os.getenv("BASE", "https://railspaapi.shohoz.com/v1.0/web"),
}

# ---------------- End config ----------------

ENDPOINTS = {
    "SIGNIN": f"{CONFIG['BASE']}/auth/sign-in",
    "SEARCH": f"{CONFIG['BASE']}/bookings/search-trips-v2",
    "SEAT_LAYOUT": f"{CONFIG['BASE']}/bookings/seat-layout",
    "RESERVE": f"{CONFIG['BASE']}/bookings/reserve-seat",
    "PASSENGER_DETAILS": f"{CONFIG['BASE']}/bookings/passenger-details",
    "VERIFY_OTP": f"{CONFIG['BASE']}/bookings/verify-otp",
    "CONFIRM": f"{CONFIG['BASE']}/bookings/confirm",
}

# ---------------- helpers ----------------
def fatal(msg: str, data: Any = None):
    print("[rail][FATAL]", msg, file=sys.stderr)
    if data is not None:
        try:
            print(json.dumps(data, indent=2, ensure_ascii=False), file=sys.stderr)
        except Exception:
            print(repr(data), file=sys.stderr)
    sys.exit(1)

def log(*args):
    print("[rail]", *args)

def session_headers(token: str = None) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Device-Id": CONFIG["DEVICE_ID"],
        "Referer": CONFIG["REFERER"],
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def http_req(session: requests.Session, method: str, url: str, token: str = None, params=None, json_body=None):
    headers = session_headers(token)
    try:
        if method.lower() == "get":
            r = session.get(url, headers=headers, params=params, timeout=CONFIG["REQUEST_TIMEOUT"])
        elif method.lower() == "post":
            r = session.post(url, headers=headers, json=json_body, timeout=CONFIG["REQUEST_TIMEOUT"])
        elif method.lower() == "patch":
            r = session.patch(url, headers=headers, json=json_body, timeout=CONFIG["REQUEST_TIMEOUT"])
        else:
            raise ValueError("Unsupported method: " + method)
    except requests.RequestException as e:
        fatal(f"Network error for {url}: {e}")
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body

# ---------------- seat / train helpers ----------------
def is_available_flag(val):
    return val is True or val == 1 or val == "1"

def findAvailableSeats(seatLayoutResponse: Dict[str, Any], needed: int, preferredCoaches: List[str], preferredSeats: List[int]):
    seatLayout = None
    try:
        seatLayout = seatLayoutResponse.get("data", {}).get("seatLayout")
    except Exception:
        seatLayout = None
    if not seatLayout:
        return []

    availableSeats = []

    # normalize preferred coaches for comparison (upper-case)
    coachesHaveValue = isinstance(preferredCoaches, list) and len(preferredCoaches) > 0
    preferred_coach_set = set(s.strip().upper() for s in preferredCoaches) if coachesHaveValue else None

    seatsHaveValue = isinstance(preferredSeats, list) and len(preferredSeats) > 0
    preferredSeatNumbers = set(map(str, preferredSeats)) if seatsHaveValue else None

    # choose coaches to iterate: if coaches specified, restrict; otherwise all
    coachesToSearch = seatLayout
    if coachesHaveValue:
        coachesToSearch = [c for c in seatLayout if str(c.get("floor_name") or "").strip().upper() in preferred_coach_set]

    # iterate coaches in order and accumulate across coaches until 'needed' found
    log_mode_parts = []
    if coachesHaveValue:
        log_mode_parts.append(f"coaches [{', '.join(preferredCoaches)}]")
    if seatsHaveValue:
        log_mode_parts.append(f"seat-numbers [{', '.join(map(str, preferredSeats))}]")
    if log_mode_parts:
        log(f"Mode: Searching for {needed} seats with {', '.join(log_mode_parts)}...")
    else:
        log(f"Mode: Searching for any {needed} available seats...")

    for coach in coachesToSearch:
        layout = coach.get("layout") or []
        for row in layout:
            if not isinstance(row, list):
                continue
            for seat in row:
                if len(availableSeats) >= needed:
                    return availableSeats[:needed]
                if not seat:
                    continue
                if not is_available_flag(seat.get("seat_availability")):
                    continue
                sn = str(seat.get("seat_number") or "").strip()
                if not sn:
                    continue
                # get suffix number after '-' if present
                seatNumPart = sn.split("-")[-1]

                # if seat numbers are restricted, check
                if preferredSeatNumbers and seatNumPart not in preferredSeatNumbers:
                    continue

                # seat matches preferences -> collect
                availableSeats.append({
                    "ticket_id": seat.get("ticket_id"),
                    "seat_number": sn,
                    "coach": coach.get("floor_name")
                })
                if len(availableSeats) >= needed:
                    return availableSeats[:needed]

    return availableSeats[:needed]

def findTripForSeatClass(trains: List[Dict[str, Any]], seatClass: str, neededSeats: int):
    if not trains:
        return None
    for t in trains:
        if not isinstance(t.get("seat_types"), list):
            continue
        for st in t.get("seat_types", []):
            if str(st.get("type", "")).upper() == str(seatClass).upper():
                try:
                    online_count = int(st.get("seat_counts", {}).get("online", 0))
                except Exception:
                    online_count = 0
                if online_count >= int(neededSeats):
                    log(f'Found train "{t.get("trip_number")}" with {online_count} available seats.')
                    return {
                        "trip_id": st.get("trip_id"),
                        "trip_route_id": st.get("trip_route_id"),
                        "train_label": t.get("trip_number") or t.get("train_model"),
                        "boarding_point_id": (t.get("boarding_points") or [{}])[0].get("trip_point_id")
                    }
                else:
                    log(f'Skipping train "{t.get("trip_number")}": not enough seats (found {online_count}, need {neededSeats}).')
    return None

# ---------------- main flow ----------------
def main():
    session = requests.Session()
    log("STARTING flow")

    # 1) sign-in
    log("1) Signing in...")
    status, body = http_req(session, "post", ENDPOINTS["SIGNIN"], json_body={
        "mobile_number": CONFIG["MOBILE"],
        "password": CONFIG["PASSWORD"],
    })
    if status < 200 or status >= 300:
        fatal("Sign-in failed", body)
    token = (body.get("data") or {}).get("token") if isinstance(body, dict) else None
    if not token:
        fatal("Sign-in failed (no token)", body)
    log("Signed in.")

    # 2) search
    log(f'2) Searching trips {CONFIG["FROM_CITY"]} -> {CONFIG["TO_CITY"]} on {CONFIG["DATE_OF_JOURNEY"]}...')
    status, body = http_req(session, "get", ENDPOINTS["SEARCH"], token=token, params={
        "from_city": CONFIG["FROM_CITY"],
        "to_city": CONFIG["TO_CITY"],
        "date_of_journey": CONFIG["DATE_OF_JOURNEY"],
        "seat_class": CONFIG["SEAT_CLASS"],
    })
    if status < 200 or status >= 300:
        fatal("Search failed", body)
    trains = (body.get("data") or {}).get("trains") if isinstance(body, dict) else None
    if not trains:
        fatal("No trains found for this route.", body)

    # TRAIN_NAME exact-match filter (supports comma-separated list)
    if CONFIG["TRAIN_NAME"]:
        log(f'Filtering for exact train name(s): "{CONFIG["TRAIN_NAME"]}"')
        want_list = [s.strip().upper() for s in re.split(r",\s*", CONFIG["TRAIN_NAME"]) if s.strip()]
        want_set = set(want_list)
        trains = [t for t in trains if str(t.get("trip_number") or "").strip().upper() in want_set]
        if not trains:
            fatal(f'The specified train name(s) "{CONFIG["TRAIN_NAME"]}" were not found (exact match required).')

    trip = findTripForSeatClass(trains, CONFIG["SEAT_CLASS"], CONFIG["NEED_SEATS"])
    if not trip:
        fatal(f'No train found with at least {CONFIG["NEED_SEATS"]} available seats of class "{CONFIG["SEAT_CLASS"]}".')
    log("Selected trip:", trip.get("train_label"))

    # 3) seat layout
    log("3) Fetching and filtering seat layout...")
    status, seat_layout_resp = http_req(session, "get", ENDPOINTS["SEAT_LAYOUT"], token=token, params={
        "trip_id": trip["trip_id"],
        "trip_route_id": trip["trip_route_id"]
    })
    if status < 200 or status >= 300:
        fatal("Seat-layout failed", seat_layout_resp)

    availableSeats = findAvailableSeats(seat_layout_resp, CONFIG["NEED_SEATS"], CONFIG["PREFERRED_COACHES"], CONFIG["PREFERRED_SEATS"])
    if len(availableSeats) < CONFIG["NEED_SEATS"]:
        fatal(f'Could not find enough seats matching your preferences. Found {len(availableSeats)}, needed {CONFIG["NEED_SEATS"]}.')

    seatNumbers = ", ".join([s["seat_number"] for s in availableSeats])
    reserved = [s["ticket_id"] for s in availableSeats]
    log(f'Found {len(availableSeats)} seats: {seatNumbers}')

    # 4) reserve (PATCH)
    log("4) Reserving seats...")
    for tid in reserved:
        status, resp = http_req(session, "patch", ENDPOINTS["RESERVE"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
        if status >= 300 or (isinstance(resp, dict) and resp.get("data", {}).get("error")):
            fatal(f"Reserve-seat failed for {tid}", resp)
    log(f"Reserved {len(reserved)} seats.")

    # 5) passenger-details -> trigger OTP
    log("5) Triggering OTP send...")
    passengerPayload = {
        "trip_id": trip["trip_id"],
        "trip_route_id": trip["trip_route_id"],
        "ticket_ids": reserved
    }
    status, resp = http_req(session, "post", ENDPOINTS["PASSENGER_DETAILS"], token=token, json_body=passengerPayload)
    if status < 200 or status >= 300:
        fatal("Passenger-details request failed", resp)
    if not (isinstance(resp, dict) and resp.get("data", {}).get("success")):
        fatal("API error while triggering OTP", resp)
    log(f'OTP sent to your phone: "{resp.get("data", {}).get("msg")}"')

    # read OTP from user
    otp = input("Please enter the OTP you received: ").strip()
    if not otp or not re.match(r"^\d{4,6}$", otp):
        fatal("Invalid OTP entered.")

    # 6) verify OTP
    log("6) Verifying OTP...")
    otpPayload = dict(passengerPayload)
    otpPayload["otp"] = otp
    status, resp = http_req(session, "post", ENDPOINTS["VERIFY_OTP"], token=token, json_body=otpPayload)
    if status < 200 or status >= 300:
        fatal("OTP verification request failed", resp)
    if not (isinstance(resp, dict) and resp.get("data", {}).get("success")):
        fatal("OTP verification failed", resp)
    mainPassenger = resp["data"].get("user")
    log("OTP Verified for user:", mainPassenger.get("name"))

    # build passenger details
    passengerDetails = {
        "pname": [mainPassenger.get("name")],
        "passengerType": ["Adult"],
        "gender": ["male"]
    }
    if CONFIG["NEED_SEATS"] > 1:
        log(f'Please enter details for the other {CONFIG["NEED_SEATS"] - 1} passenger(s).')
        for i in range(1, CONFIG["NEED_SEATS"]):
            name = input(f"  - Passenger {i+1} Name: ").strip()
            ptype = input(f"  - Passenger {i+1} Type (Adult/Child): ").strip() or "Adult"
            gender = input(f"  - Passenger {i+1} Gender (Male/Female): ").strip().lower() or "male"
            passengerDetails["pname"].append(name)
            passengerDetails["passengerType"].append(ptype)
            passengerDetails["gender"].append(gender)

    # review
    print("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====")
    print("Train:         ", trip.get("train_label"))
    print("From:          ", CONFIG["FROM_CITY"])
    print("To:            ", CONFIG["TO_CITY"])
    print("Date:          ", CONFIG["DATE_OF_JOURNEY"])
    print("Class:         ", CONFIG["SEAT_CLASS"])
    print("Total Seats:   ", len(availableSeats))
    print("Seat Numbers:  ", seatNumbers)
    print("\nPassengers:")
    for i, nm in enumerate(passengerDetails["pname"]):
        print(f"  - {nm} ({passengerDetails['passengerType'][i]}, {passengerDetails['gender'][i]})")

    confirm = input("Proceed to payment? (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        log("Booking cancelled by user.")
        sys.exit(0)

    # 9) confirm booking (PATCH)
    log("9) Confirming booking to get payment link...")
    seats_to_find = len(passengerDetails["pname"])
    nullsArray = [None] * seats_to_find
    emptyStrArray = [""] * seats_to_find

    confirmPayload = {
        **passengerPayload,
        "otp": otp,
        "boarding_point_id": trip.get("boarding_point_id"),
        "pname": passengerDetails["pname"],
        "passengerType": passengerDetails["passengerType"],
        "gender": passengerDetails["gender"],
        "pemail": mainPassenger.get("email"),
        "pmobile": mainPassenger.get("mobile"),
        "contactperson": 0,
        "enable_sms_alert": 0,
        "seat_class": CONFIG["SEAT_CLASS"],
        "from_city": CONFIG["FROM_CITY"],
        "to_city": CONFIG["TO_CITY"],
        "date_of_journey": CONFIG["DATE_OF_JOURNEY"],
        "is_bkash_online": True,
        "selected_mobile_transaction": 1,
        "date_of_birth": nullsArray,
        "first_name": nullsArray,
        "last_name": nullsArray,
        "middle_name": nullsArray,
        "nationality": nullsArray,
        "page": emptyStrArray,
        "ppassport": emptyStrArray,
        "passport_expiry_date": nullsArray,
        "passport_no": nullsArray,
        "passport_type": nullsArray,
        "visa_expire_date": nullsArray,
        "visa_issue_date": nullsArray,
        "visa_issue_place": nullsArray,
        "visa_no": nullsArray,
        "visa_type": nullsArray,
    }
    status, resp = http_req(session, "patch", ENDPOINTS["CONFIRM"], token=token, json_body=confirmPayload)
    if status < 200 or status >= 300:
        fatal("Confirm booking failed", resp)
    redirectUrl = (resp.get("data") or {}).get("redirectUrl")
    if not redirectUrl:
        fatal("Could not get payment URL from confirmation response", resp)

    log("✅ Booking Confirmed!")
    log("Opening payment link in browser:", redirectUrl)
    try:
        webbrowser.open(redirectUrl)
    except Exception as e:
        log("Could not open browser automatically. Payment URL:", redirectUrl, "Error:", e)

    log("Please complete payment in your browser.")
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fatal("Interrupted by user")
