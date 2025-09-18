#!/usr/bin/env python3
"""
rail_booking.py

Converted from rail_booking.js (final version).
Requires:
  pip install requests python-dotenv
Environment variables (required):
  MOBILE, PASSWORD, FROM_CITY, TO_CITY, DATE_OF_JOURNEY, SEAT_CLASS, NEED_SEATS
Optional env:
  TRAIN_NAME, PREFERRED_COACHES, PREFERRED_SEATS, REQUEST_TIMEOUT, DEVICE_ID, REFERER, BASE
"""

import os
import sys
import json
import re
import requests
import webbrowser
from typing import Any, Dict, List, Optional

# optional: load .env if python-dotenv available
try:
    from dotenv import load_dotenv

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except Exception:
    pass

# ---------------- helpers ----------------

class BookingError(Exception):
    """Custom exception for booking failures, carrying details for logging."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details

def log(*args):
    """Prints a standard log message."""
    print("[rail]", *args)


def fatal(msg: str, data: Any = None):
    """
    Prints a fatal error message and exits the script.
    If `data` is supplied, it's printed as JSON for detailed diagnostics.
    """
    print("[rail][FATAL]", msg, file=sys.stderr)
    if data is not None:
        try:
            print("[rail][DETAILS]", file=sys.stderr)
            print(json.dumps(data, indent=2, ensure_ascii=False), file=sys.stderr)
        except Exception:
            # Fallback to repr if JSON serialization fails
            print("[rail][DETAILS]", repr(data), file=sys.stderr)
    sys.exit(1)


def parse_list_env(name: str) -> List[str]:
    """Parses a comma-separated string from an environment variable into a list."""
    v = os.getenv(name, "") or ""
    if not v:
        return []
    return [s.strip() for s in re.split(r",\s*", v) if s.strip()]


def parse_int_like_list_env(name: str) -> List[str]:
    """Parses a comma-separated list of numbers-as-strings from an env variable."""
    v = os.getenv(name, "") or ""
    if not v:
        return []
    return [s.strip() for s in re.split(r",\s*", v) if s.strip()]


def session_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Constructs the necessary HTTP headers for API requests."""
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


def http_req(session: requests.Session, method: str, url: str, token: Optional[str] = None, params: Any = None, json_body: Any = None):
    """
    Makes an HTTP request and handles potential network errors and response parsing.
    """
    headers = session_headers(token)
    timeout = CONFIG.get("REQUEST_TIMEOUT", 20)
    try:
        if method.lower() == "get":
            r = session.get(url, headers=headers, params=params, timeout=timeout)
        elif method.lower() == "post":
            r = session.post(url, headers=headers, json=json_body, timeout=timeout)
        elif method.lower() == "patch":
            r = session.patch(url, headers=headers, json=json_body, timeout=timeout)
        else:
            raise ValueError("Unsupported method: " + method)
    except requests.RequestException as e:
        raise RuntimeError(f"Network error for {url}: {e}")
    
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


def is_available_flag(val):
    """Checks if a seat availability flag is positive."""
    return val is True or val == 1 or val == "1"


def find_available_seats(seat_layout_resp: Dict[str, Any], needed: int, preferred_coaches: List[str], preferred_seats: List[str]):
    """Finds available seats based on user preferences."""
    seat_layout = seat_layout_resp.get("data", {}).get("seatLayout")
    if not seat_layout:
        return []

    available = []
    coaches_have = bool(preferred_coaches)
    seats_have = bool(preferred_seats)

    coach_set = set((c or "").strip().lower() for c in preferred_coaches) if coaches_have else None
    seat_set = set(str(s) for s in preferred_seats) if seats_have else None

    if coaches_have:
        log("Mode: Searching only in preferred coaches:", ", ".join(preferred_coaches))
    if seats_have:
        log("Mode: Searching only for preferred seat numbers:", ", ".join(preferred_seats))

    coaches_to_search = seat_layout
    if coaches_have:
        coaches_to_search = [c for c in seat_layout if (str(c.get("floor_name") or "").strip().lower() in coach_set)]

    for coach in coaches_to_search:
        for row in coach.get("layout", []) or []:
            for seat in row or []:
                if len(available) >= needed:
                    return available[:needed]
                if not seat or not is_available_flag(seat.get("seat_availability")):
                    continue
                sn = str(seat.get("seat_number") or "").strip()
                if not sn:
                    continue
                seat_num_part = sn.split("-")[-1]
                if seat_set and seat_num_part not in seat_set:
                    continue
                available.append({"ticket_id": seat.get("ticket_id"), "seat_number": sn})
    return available[:needed]


def find_trip_for_seat_class(trains: List[Dict[str, Any]], seat_class: str, needed_seats: int):
    """Finds a suitable train trip that has enough available seats."""
    if not trains:
        return None
    for t in trains:
        if not isinstance(t.get("seat_types"), list):
            continue
        for st in t.get("seat_types") or []:
            if str(st.get("type") or "").lower() == str(seat_class).lower():
                try:
                    online = int(st.get("seat_counts", {}).get("online", 0))
                except (ValueError, TypeError):
                    online = 0
                if online >= int(needed_seats):
                    log(f'Found train "{t.get("trip_number")}" with {online} available seats.')
                    return {
                        "trip_id": st.get("trip_id"),
                        "trip_route_id": st.get("trip_route_id"),
                        "train_label": t.get("trip_number") or t.get("train_model"),
                        "boarding_point_id": (t.get("boarding_points") or [{}])[0].get("trip_point_id"),
                    }
                else:
                    log(f'Skipping train "{t.get("trip_number")}": not enough seats (found {online}, need {needed_seats}).')
    return None


# ---------------- CONFIG ----------------
try:
    REQUIRED_ENV = ["MOBILE", "PASSWORD", "FROM_CITY", "TO_CITY", "DATE_OF_JOURNEY", "SEAT_CLASS", "NEED_SEATS"]
    missing = [k for k in REQUIRED_ENV if not (os.getenv(k) or "").strip()]
    if missing:
        # No need to raise an exception here, fatal is fine as no state needs cleanup.
        fatal("Missing required environment variables:", ", ".join(missing))

    CONFIG = {
        "MOBILE": os.getenv("MOBILE", ""),
        "PASSWORD": os.getenv("PASSWORD", ""),
        "FROM_CITY": os.getenv("FROM_CITY", ""),
        "TO_CITY": os.getenv("TO_CITY", ""),
        "DATE_OF_JOURNEY": os.getenv("DATE_OF_JOURNEY", ""),
        "SEAT_CLASS": (os.getenv("SEAT_CLASS", "") or "").lower(),
        "NEED_SEATS": int(os.getenv("NEED_SEATS", "1")),
        "TRAIN_NAME": (os.getenv("TRAIN_NAME", "") or "").lower(),
        "PREFERRED_COACHES": parse_list_env("PREFERRED_COACHES"),
        "PREFERRED_SEATS": parse_int_like_list_env("PREFERRED_SEATS"),
        "REQUEST_TIMEOUT": int(os.getenv("REQUEST_TIMEOUT", "20")),
        "DEVICE_ID": os.getenv("DEVICE_ID", "4004028937"),
        "REFERER": os.getenv("REFERER", "https://eticket.railway.gov.bd/"),
        "BASE": os.getenv("BASE", "https://railspaapi.shohoz.com/v1.0/web"),
    }

    ENDPOINTS = {
        "SIGNIN": f"{CONFIG['BASE']}/auth/sign-in",
        "SEARCH": f"{CONFIG['BASE']}/bookings/search-trips-v2",
        "SEAT_LAYOUT": f"{CONFIG['BASE']}/bookings/seat-layout",
        "RESERVE": f"{CONFIG['BASE']}/bookings/reserve-seat",
        "RELEASE_SEAT": f"{CONFIG['BASE']}/bookings/release-seat",
        "PASSENGER_DETAILS": f"{CONFIG['BASE']}/bookings/passenger-details",
        "VERIFY_OTP": f"{CONFIG['BASE']}/bookings/verify-otp",
        "CONFIRM": f"{CONFIG['BASE']}/bookings/confirm",
    }
except ValueError as e:
    fatal("Configuration error: A numeric value in your environment variables is invalid.", str(e))
except Exception as e:
    fatal("An unexpected error occurred during initialization.", str(e))


# ---------------- main flow ----------------
def main():
    """Main function to execute the booking flow."""
    session = requests.Session()
    token = None
    trip = None
    successfully_reserved: List[str] = []
    otp_payload: Optional[Dict[str, Any]] = None

    try:
        log("STARTING flow")

        # 1) Sign in
        log("1) Signing in...")
        status, body = http_req(session, "post", ENDPOINTS["SIGNIN"], json_body={
            "mobile_number": CONFIG["MOBILE"],
            "password": CONFIG["PASSWORD"],
        })
        if status != 200:
            raise BookingError("Sign-in failed", body)
        token = (body.get("data") or {}).get("token") if isinstance(body, dict) else None
        if not token:
            raise BookingError("Sign-in failed (no token)", body)
        log("Signed in.")

        # 2) Search trips
        log(f'2) Searching trips {CONFIG["FROM_CITY"]} -> {CONFIG["TO_CITY"]}...')
        status, body = http_req(session, "get", ENDPOINTS["SEARCH"], token=token, params={
            "from_city": CONFIG["FROM_CITY"],
            "to_city": CONFIG["TO_CITY"],
            "date_of_journey": CONFIG["DATE_OF_JOURNEY"],
            "seat_class": CONFIG["SEAT_CLASS"],
        })
        if status != 200:
            raise BookingError("Search failed", body)
        trains = (body.get("data") or {}).get("trains") if isinstance(body, dict) else []
        if not trains:
            raise BookingError("No trains found for this route.", body)

        if CONFIG["TRAIN_NAME"]:
            trains = [t for t in trains if CONFIG["TRAIN_NAME"] in str(t.get("trip_number") or "").lower()]
            if not trains:
                raise BookingError(f'The specified train "{CONFIG["TRAIN_NAME"]}" was not found.', body)

        trip = find_trip_for_seat_class(trains, CONFIG["SEAT_CLASS"], CONFIG["NEED_SEATS"])
        if not trip:
            raise BookingError(f'No train found with at least {CONFIG["NEED_SEATS"]} available seats of class "{CONFIG["SEAT_CLASS"]}".', body)
        log("Selected trip:", trip.get("train_label"))

        # 3) Get seat layout
        log("3) Fetching and filtering seat layout...")
        status, seat_layout_resp = http_req(session, "get", ENDPOINTS["SEAT_LAYOUT"], token=token, params={
            "trip_id": trip["trip_id"],
            "trip_route_id": trip["trip_route_id"]
        })
        if status != 200:
            raise BookingError("Seat layout fetch failed", seat_layout_resp)

        available_seats = find_available_seats(seat_layout_resp, CONFIG["NEED_SEATS"], CONFIG["PREFERRED_COACHES"], CONFIG["PREFERRED_SEATS"])
        if len(available_seats) < CONFIG["NEED_SEATS"]:
            raise BookingError(f"Could not find enough seats matching preferences. Found {len(available_seats)}, needed {CONFIG['NEED_SEATS']}.", seat_layout_resp)

        seat_numbers = ", ".join([s["seat_number"] for s in available_seats])
        ticket_ids = [s["ticket_id"] for s in available_seats]
        log(f'Found {len(available_seats)} seats: {seat_numbers}')

        # 4) Reserve each ticket
        log("4) Reserving seats...")
        for tid in ticket_ids:
            status, resp = http_req(session, "patch", ENDPOINTS["RESERVE"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
            if status != 200 or (isinstance(resp, dict) and resp.get("data", {}).get("error")):
                raise BookingError(f"Failed to reserve ticket ID {tid}.", resp)
            log(f"  - Successfully reserved ticket ID: {tid}")
            successfully_reserved.append(tid)
        log(f"Successfully reserved all {len(successfully_reserved)} seats.")

        # 5) Trigger OTP
        log("5) Triggering OTP send...")
        passenger_payload = {"trip_id": trip["trip_id"], "trip_route_id": trip["trip_route_id"], "ticket_ids": successfully_reserved}
        status, resp = http_req(session, "post", ENDPOINTS["PASSENGER_DETAILS"], token=token, json_body=passenger_payload)
        if status != 200 or not (isinstance(resp, dict) and resp.get("data", {}).get("success")):
            raise BookingError("API error while triggering OTP", resp)
        log(f'OTP sent to your phone: "{resp.get("data", {}).get("msg")}"')

        # 6) Verify OTP
        otp_verified = False
        main_passenger = None
        last_otp_resp = None
        for attempt in range(1, 4):
            otp_input = input(f"Please enter the OTP you received (Attempt {attempt}/3): ").strip()
            if not otp_input or not re.match(r"^\d{4,6}$", otp_input):
                log("Invalid OTP format. Please try again.")
                continue
            log(f"Verifying OTP (Attempt {attempt}/3)...")
            otp_payload = dict(passenger_payload)
            otp_payload["otp"] = otp_input
            status, otp_resp = http_req(session, "post", ENDPOINTS["VERIFY_OTP"], token=token, json_body=otp_payload)
            last_otp_resp = otp_resp
            if status == 200 and isinstance(otp_resp, dict) and otp_resp.get("data", {}).get("success"):
                main_passenger = otp_resp["data"].get("user")
                log("✅ OTP Verified for user:", main_passenger.get("name"))
                otp_verified = True
                break
            else:
                log("Incorrect OTP. Please try again.")
        if not otp_verified:
            raise BookingError("OTP verification failed after 3 attempts.", last_otp_resp)

        # 7) Get passenger details
        passenger_details = {"pname": [main_passenger.get("name")], "passengerType": ["Adult"], "gender": ["male"]}
        if CONFIG["NEED_SEATS"] > 1:
            log(f'Please enter details for the other {CONFIG["NEED_SEATS"] - 1} passenger(s).')
            for i in range(1, CONFIG["NEED_SEATS"]):
                name = input(f"  - Passenger {i+1} Name: ").strip()
                ptype = input(f"  - Passenger {i+1} Type (Adult/Child): ").strip() or "Adult"
                gender = input(f"  - Passenger {i+1} Gender (Male/Female): ").strip().lower() or "male"
                passenger_details["pname"].append(name)
                passenger_details["passengerType"].append(ptype)
                passenger_details["gender"].append(gender)

        # 8) Review and confirm
        print("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====")
        print(f"Train:          {trip.get('train_label')}")
        print(f"From:           {CONFIG['FROM_CITY']}")
        print(f"To:             {CONFIG['TO_CITY']}")
        print(f"Date:           {CONFIG['DATE_OF_JOURNEY']}")
        print(f"Class:          {CONFIG['SEAT_CLASS']}")
        print(f"Total Seats:    {len(available_seats)}")
        print(f"Seat Numbers:   {seat_numbers}")
        print("\nPassengers:")
        for i, nm in enumerate(passenger_details["pname"]):
            print(f"  - {nm} ({passenger_details['passengerType'][i]}, {passenger_details['gender'][i]})")

        confirm = input("\nProceed to payment? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            raise BookingError("Booking cancelled by user.")

        # 9) Confirm booking to get payment link
        log("9) Confirming booking to get payment link...")
        confirm_payload = {
            **passenger_payload,
            "otp": otp_payload["otp"],
            "boarding_point_id": trip.get("boarding_point_id"),
            "pname": passenger_details["pname"],
            "passengerType": passenger_details["passengerType"],
            "gender": passenger_details["gender"],
            "pemail": main_passenger.get("email"),
            "pmobile": main_passenger.get("mobile"),
            "is_bkash_online": True,
        }
        status, resp = http_req(session, "patch", ENDPOINTS["CONFIRM"], token=token, json_body=confirm_payload)
        if status != 200:
            raise BookingError("Confirm booking failed", resp)
        redirect_url = (resp.get("data") or {}).get("redirectUrl") if isinstance(resp, dict) else None
        if not redirect_url:
            raise BookingError("Could not get payment URL from confirmation response", resp)

        log("✅ Booking Confirmed!")
        log("Opening payment link in your browser...")
        try:
            webbrowser.open(redirect_url)
        except Exception:
            log("Could not open browser automatically. Payment URL:", redirect_url)
        log("Please complete payment in your browser.")

    except BookingError as e:
        if successfully_reserved:
            log(f"An error occurred: {e}. Attempting to release {len(successfully_reserved)} reserved seat(s)...")
            for tid in successfully_reserved:
                try:
                    if trip:
                        http_req(session, "patch", ENDPOINTS["RELEASE_SEAT"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
                        log(f"  - Released ticket ID: {tid}")
                    else:
                        log(f"  - Cannot release ticket ID {tid}: trip info is missing.")
                except Exception as release_exc:
                    log(f"  - Failed to release ticket ID {tid}: {release_exc}")
            log("Release attempts finished.")

        short_msg = str(e)
        details = e.details
        if isinstance(details, dict) and details.get("error"):
            error_data = details.get("error", {})
            code = error_data.get("code", "N/A")
            messages = error_data.get("messages")
            first_msg = (messages[0] if isinstance(messages, list) and messages else "Unknown server error")
            short_msg = f"Booking failed — code {code}: {first_msg}"
        fatal(short_msg, details)

    except Exception as e:
        if successfully_reserved:
            log(f"An unexpected error occurred: {e}. Attempting to release {len(successfully_reserved)} reserved seat(s)...")
            for tid in successfully_reserved:
                try:
                    if trip:
                        http_req(session, "patch", ENDPOINTS["RELEASE_SEAT"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
                        log(f"  - Released ticket ID: {tid}")
                    else:
                        log(f"  - Cannot release ticket ID {tid}: trip info is missing.")
                except Exception as release_exc:
                    log(f"  - Failed to release ticket ID {tid}: {release_exc}")
            log("Release attempts finished.")

        fatal(f"An unexpected runtime error occurred: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fatal("Interrupted by user. Exiting.")
    except Exception as e:
        fatal(f"An unhandled critical error occurred: {e}", getattr(e, 'details', None))

