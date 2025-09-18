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
def log(*args):
    print("[rail]", *args)


def fatal(msg: str, data: Any = None):
    """
    Print fatal message and, if `data` supplied, also print details as JSON
    similar to the Node.js behavior:
      [rail][FATAL] <msg>
      [rail][DETAILS] <json...>
    """
    print("[rail][FATAL]", msg, file=sys.stderr)
    if data is not None:
        try:
            print("[rail][DETAILS]", file=sys.stderr)
            print(json.dumps(data, indent=2, ensure_ascii=False), file=sys.stderr)
        except Exception:
            # fallback to repr if JSON serialization fails
            print("[rail][DETAILS]", repr(data), file=sys.stderr)
    sys.exit(1)


def parse_list_env(name: str) -> List[str]:
    v = os.getenv(name, "") or ""
    if not v:
        return []
    return [s.strip() for s in re.split(r",\s*", v) if s.strip()]


def parse_int_like_list_env(name: str) -> List[str]:
    v = os.getenv(name, "") or ""
    if not v:
        return []
    return [s.strip() for s in re.split(r",\s*", v) if s.strip()]


def session_headers(token: Optional[str] = None) -> Dict[str, str]:
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
    except Exception:
        body = r.text
    return r.status_code, body


def is_available_flag(val):
    return val is True or val == 1 or val == "1"


def find_available_seats(seat_layout_resp: Dict[str, Any], needed: int, preferred_coaches: List[str], preferred_seats: List[str]):
    seat_layout = None
    try:
        seat_layout = seat_layout_resp.get("data", {}).get("seatLayout")
    except Exception:
        seat_layout = None
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
                if not seat:
                    continue
                if not is_available_flag(seat.get("seat_availability")):
                    continue
                sn = str(seat.get("seat_number") or "").strip()
                if not sn:
                    continue
                seat_num_part = sn.split("-")[-1]
                if seat_set and seat_num_part not in seat_set:
                    continue
                available.append({"ticket_id": seat.get("ticket_id"), "seat_number": sn})
                if len(available) >= needed:
                    return available[:needed]
    return available[:needed]


def find_trip_for_seat_class(trains: List[Dict[str, Any]], seat_class: str, needed_seats: int):
    if not trains:
        return None
    for t in trains:
        if not isinstance(t.get("seat_types"), list):
            continue
        for st in t.get("seat_types") or []:
            if str(st.get("type") or "").lower() == str(seat_class).lower():
                try:
                    online = int(st.get("seat_counts", {}).get("online", 0))
                except Exception:
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
REQUIRED_ENV = ["MOBILE", "PASSWORD", "FROM_CITY", "TO_CITY", "DATE_OF_JOURNEY", "SEAT_CLASS", "NEED_SEATS"]
missing = [k for k in REQUIRED_ENV if (os.getenv(k) or "").strip() == ""]
if missing:
    print("[rail][FATAL] Missing required environment variables:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)

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

# ---------------- main flow ----------------
def main():
    session = requests.Session()
    token = None
    trip = None
    successfully_reserved: List[str] = []
    otp_payload: Optional[Dict[str, Any]] = None

    try:
        log("STARTING flow")

        # 1) sign in
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

        # 2) search trips
        log(f'2) Searching trips {CONFIG["FROM_CITY"]} -> {CONFIG["TO_CITY"]}...')
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

        # optional train name filter (substring match)
        if CONFIG["TRAIN_NAME"]:
            trains = [t for t in trains if CONFIG["TRAIN_NAME"] in str(t.get("trip_number") or "").lower()]
            if not trains:
                fatal(f'The specified train "{CONFIG["TRAIN_NAME"]}" was not found.', body)

        trip = find_trip_for_seat_class(trains, CONFIG["SEAT_CLASS"], CONFIG["NEED_SEATS"])
        if not trip:
            fatal(f'No train found with at least {CONFIG["NEED_SEATS"]} available seats of class "{CONFIG["SEAT_CLASS"]}".', body)
        log("Selected trip:", trip.get("train_label"))

        # 3) seat layout
        log("3) Fetching and filtering seat layout...")
        status, seat_layout_resp = http_req(session, "get", ENDPOINTS["SEAT_LAYOUT"], token=token, params={
            "trip_id": trip["trip_id"],
            "trip_route_id": trip["trip_route_id"]
        })
        if status < 200 or status >= 300:
            fatal("Seat layout fetch failed", seat_layout_resp)

        available_seats = find_available_seats(seat_layout_resp, CONFIG["NEED_SEATS"], CONFIG["PREFERRED_COACHES"], CONFIG["PREFERRED_SEATS"])
        if len(available_seats) < CONFIG["NEED_SEATS"]:
            details = seat_layout_resp if isinstance(seat_layout_resp, dict) else None
            fatal(f"Could not find enough seats matching preferences. Found {len(available_seats)}, needed {CONFIG['NEED_SEATS']}.", details)

        seat_numbers = ", ".join([s["seat_number"] for s in available_seats])
        ticket_ids = [s["ticket_id"] for s in available_seats]
        log(f'Found {len(available_seats)} seats: {seat_numbers}')

        # 4) reserve each ticket
        log("4) Reserving seats...")
        for tid in ticket_ids:
            status, resp = http_req(session, "patch", ENDPOINTS["RESERVE"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
            if status >= 300 or (isinstance(resp, dict) and resp.get("data", {}).get("error")):
                fatal(f"Failed to reserve ticket ID {tid}.", resp)
            log(f"  - Successfully reserved ticket ID: {tid}")
            successfully_reserved.append(tid)
        log(f"Successfully reserved all {len(successfully_reserved)} seats.")

        # 5) trigger OTP
        log("5) Triggering OTP send...")
        passenger_payload = {"trip_id": trip["trip_id"], "trip_route_id": trip["trip_route_id"], "ticket_ids": successfully_reserved}
        status, resp = http_req(session, "post", ENDPOINTS["PASSENGER_DETAILS"], token=token, json_body=passenger_payload)
        if status < 200 or status >= 300 or not (isinstance(resp, dict) and resp.get("data", {}).get("success")):
            fatal("API error while triggering OTP", resp)
        log(f'OTP sent to your phone: "{resp.get("data", {}).get("msg")}"')

        # OTP loop (up to 3 attempts)
        otp_verified = False
        main_passenger = None
        last_otp_resp = None
        for attempt in range(1, 4):
            otp_input = input(f"Please enter the OTP you received (Attempt {attempt}/3): ").strip()
            if not otp_input or not re.match(r"^\d{4,6}$", otp_input):
                log("Invalid OTP format. Please try again.")
                continue
            log(f"6) Verifying OTP (Attempt {attempt}/3)...")
            otp_payload = dict(passenger_payload)
            otp_payload["otp"] = otp_input
            status, otp_resp = http_req(session, "post", ENDPOINTS["VERIFY_OTP"], token=token, json_body=otp_payload)
            last_otp_resp = otp_resp
            if status >= 200 and status < 300 and isinstance(otp_resp, dict) and otp_resp.get("data", {}).get("success"):
                main_passenger = otp_resp["data"].get("user")
                log("✅ OTP Verified for user:", main_passenger.get("name"))
                otp_verified = True
                break
            else:
                log("Incorrect OTP. Please try again.")
        if not otp_verified:
            details = last_otp_resp if isinstance(last_otp_resp, dict) else None
            fatal("OTP verification failed after 3 attempts.", details)

        # build passenger details
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

        # review
        print("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====")
        print("Train:         ", trip.get("train_label"))
        print("From:          ", CONFIG["FROM_CITY"])
        print("To:            ", CONFIG["TO_CITY"])
        print("Date:          ", CONFIG["DATE_OF_JOURNEY"])
        print("Class:         ", CONFIG["SEAT_CLASS"])
        print("Total Seats:   ", len(available_seats))
        print("Seat Numbers:  ", seat_numbers)
        print("\nPassengers:")
        for i, nm in enumerate(passenger_details["pname"]):
            print(f"  - {nm} ({passenger_details['passengerType'][i]}, {passenger_details['gender'][i]})")

        confirm = input("Proceed to payment? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            fatal("Booking cancelled by user.")

        # confirm booking
        log("9) Confirming booking to get payment link...")
        seats_count = len(passenger_details["pname"])
        nulls = [None] * seats_count
        empty_strs = [""] * seats_count

        confirm_payload = {
            **passenger_payload,
            "otp": otp_payload["otp"],
            "boarding_point_id": trip.get("boarding_point_id"),
            "pname": passenger_details["pname"],
            "passengerType": passenger_details["passengerType"],
            "gender": passenger_details["gender"],
            "pemail": main_passenger.get("email"),
            "pmobile": main_passenger.get("mobile"),
            "contactperson": 0,
            "enable_sms_alert": 0,
            "seat_class": CONFIG["SEAT_CLASS"],
            "from_city": CONFIG["FROM_CITY"],
            "to_city": CONFIG["TO_CITY"],
            "date_of_journey": CONFIG["DATE_OF_JOURNEY"],
            "is_bkash_online": True,
            "selected_mobile_transaction": 1,
            "date_of_birth": nulls,
            "first_name": nulls,
            "last_name": nulls,
            "middle_name": nulls,
            "nationality": nulls,
            "page": empty_strs,
            "ppassport": empty_strs,
            "passport_expiry_date": nulls,
            "passport_no": empty_strs,
            "passport_type": nulls,
            "visa_expire_date": nulls,
            "visa_issue_date": nulls,
            "visa_issue_place": nulls,
            "visa_no": nulls,
            "visa_type": nulls,
        }
        status, resp = http_req(session, "patch", ENDPOINTS["CONFIRM"], token=token, json_body=confirm_payload)
        if status < 200 or status >= 300:
            fatal("Confirm booking failed", resp)
        redirect_url = (resp.get("data") or {}).get("redirectUrl") if isinstance(resp, dict) else None
        if not redirect_url:
            fatal("Could not get payment URL from confirmation response", resp)

        log("✅ Booking Confirmed!")
        log("Opening payment link in your browser:", redirect_url)
        try:
            webbrowser.open(redirect_url)
        except Exception:
            log("Could not open browser automatically. Payment URL:", redirect_url)
        log("Please complete payment in your browser.")
        sys.exit(0)

    except Exception as e:
        # rollback: release reserved seats individually (don't fail whole flow)
        try:
            if successfully_reserved:
                log(f"Attempting to release {len(successfully_reserved)} reserved seat(s)...")
                for tid in successfully_reserved:
                    try:
                        log(f"  - Releasing ticket ID: {tid}")
                        if not ENDPOINTS.get("RELEASE_SEAT"):
                            log("    - RELEASE_SEAT endpoint not configured; skipping release.")
                            continue
                        if not trip or not trip.get("trip_route_id"):
                            log("    - trip or trip_route_id missing; cannot release ticket, skipping.")
                            continue
                        status, rel_resp = http_req(session, "patch", ENDPOINTS["RELEASE_SEAT"], token=token, json_body={"ticket_id": tid, "route_id": trip["trip_route_id"]})
                        if status >= 300:
                            log(f"    - Release request for {tid} returned status {status}")
                        else:
                            log(f"    - Released {tid}")
                    except Exception as release_exc:
                        log(f"    - Failed to release {tid}: {release_exc}")
                log("Release attempts finished.")
        except Exception as re_outer:
            log("Error while attempting releases:", re_outer)

        # prepare short error message (code + first message) if server details exist
        details = getattr(e, "details", None)
        if isinstance(details, dict) and details.get("error"):
            code = details["error"].get("code")
            first_msg = None
            msgs = details["error"].get("messages")
            if isinstance(msgs, list) and msgs:
                first_msg = msgs[0]
            short_msg = f"Booking failed — code {code}: {first_msg or 'Unknown error'}"
            # print both short message and full details (like Node.js)
            fatal(short_msg, details)
        else:
            # fallback: show exception message and, if possible, include any response body
            attached = None
            if hasattr(e, "args") and len(e.args) > 1 and isinstance(e.args[1], dict):
                attached = e.args[1]
            fatal(str(e), attached)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fatal("Interrupted by user")
