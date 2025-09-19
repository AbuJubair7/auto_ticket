#!/usr/bin/env python3
"""
rail_booking_mirror.py

Direct Python mirror of the final rail_booking.js you provided.
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
from requests.adapters import HTTPAdapter
import concurrent.futures
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


def fatal(err: Any, data: Any = None):
    print("[rail][FATAL]", err, file=sys.stderr)
    if data is not None:
        try:
            print("[rail][DETAILS]", json.dumps(data, indent=2, ensure_ascii=False), file=sys.stderr)
        except Exception:
            print("[rail][DETAILS]", repr(data), file=sys.stderr)
    sys.exit(1)


def ask_question(query: str) -> str:
    # using built-in input() for simplicity
    try:
        return input(query).strip()
    except EOFError:
        return ""


class HTTPResponse:
    """
    Small wrapper to mirror axios response interface used in JS (res.status, res.data)
    """
    def __init__(self, status: int, data: Any):
        self.status = status
        self.data = data

# Pooled HTTP session (keep-alive) similar to JS keep-alive agents
_SESSION = requests.Session()
# Tune pool sizes; can be overridden with env
_POOL_CONN = int(os.getenv("POOL_CONNECTIONS", "50") or "50")
_POOL_SIZE = int(os.getenv("POOL_MAXSIZE", "50") or "50")
_ADAPTER = HTTPAdapter(pool_connections=_POOL_CONN, pool_maxsize=_POOL_SIZE)
_SESSION.mount("http://", _ADAPTER)
_SESSION.mount("https://", _ADAPTER)


def axios_req(url: str, data: Any = None, token: Optional[str] = None, method: str = "post", config_request_timeout_ms: int = 20000) -> HTTPResponse:
    """
    Mirrors axiosReq from JS:
      - supports get/post/patch
      - returns HTTPResponse(status, data)
      - sets headers and timeout
      - raises on request errors, with timeout handled specially
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Device-Id": CONFIG["DEVICE_ID"],
        "Referer": CONFIG["REFERER"],
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout_seconds = max(1, int(config_request_timeout_ms) / 1000.0)
    method_lower = (method or "post").lower()

    try:
        if method_lower == "get":
            res = _SESSION.get(url, headers=headers, params=data, timeout=timeout_seconds)
        elif method_lower == "post":
            res = _SESSION.post(url, headers=headers, json=data, timeout=timeout_seconds)
        elif method_lower == "patch":
            res = _SESSION.patch(url, headers=headers, json=data, timeout=timeout_seconds)
        else:
            # fallback: try to send as post
            res = _SESSION.request(method_lower, url, headers=headers, json=data, timeout=timeout_seconds)
    except requests.exceptions.Timeout as te:
        # mirror JS behavior for timeout -> throw specialized error
        raise Exception(f"Request timeout for {url}")
    except requests.RequestException as e:
        # bubble other request exceptions
        raise

    # try to parse JSON, fallback to text
    try:
        body = res.json()
    except Exception:
        body = res.text

    return HTTPResponse(res.status_code, body)


def find_available_seats(seat_layout_response: Dict[str, Any], needed: int, preferred_coaches: List[str], preferred_seats: List[str]) -> List[Dict[str, Any]]:
    seat_layout = None
    if isinstance(seat_layout_response, dict):
        seat_layout = seat_layout_response.get("data", {}).get("seatLayout")
    if not seat_layout:
        return []

    available_seats: List[Dict[str, Any]] = []
    coaches_have_value = bool(preferred_coaches)
    seats_have_value = bool(preferred_seats)

    if coaches_have_value:
        lower_coaches = [c.lower().strip() for c in preferred_coaches]
        coaches_to_search = [c for c in seat_layout if (c.get("floor_name") or "").lower() in lower_coaches]
    else:
        coaches_to_search = seat_layout

    preferred_seat_numbers = set(map(str, preferred_seats)) if seats_have_value else None

    if coaches_have_value:
        log("Mode: Searching only in preferred coaches:", ", ".join(preferred_coaches))
    if seats_have_value:
        log("Mode: Searching only for preferred seat numbers:", ", ".join(preferred_seats))

    for coach in coaches_to_search:
        # JS assumes coach.layout exists and is iterable
        for row in (coach.get("layout") or []):
            for seat in (row or []):
                if len(available_seats) >= needed:
                    return available_seats
                # JS checks seat.seat_availability === 1 and seat.seat_number exists
                seat_avail = seat.get("seat_availability")
                if seat_avail == 1 and seat.get("seat_number"):
                    if preferred_seat_numbers is not None:
                        # seat_number format "X-YY" -> JS took second part via split("-")[1]
                        parts = str(seat.get("seat_number")).split("-")
                        seat_num_part = parts[1] if len(parts) > 1 else parts[-1]
                        if str(seat_num_part) in preferred_seat_numbers:
                            available_seats.append({"ticket_id": seat.get("ticket_id"), "seat_number": seat.get("seat_number")})
                    else:
                        available_seats.append({"ticket_id": seat.get("ticket_id"), "seat_number": seat.get("seat_number")})
    return available_seats


def find_trip_for_seat_class(trains: List[Dict[str, Any]], seat_class: str, needed_seats: int):
    if not trains:
        return None
    for t in trains:
        if not isinstance(t.get("seat_types"), list):
            continue
        for st in (t.get("seat_types") or []):
            if (st.get("type") or "").lower() == seat_class:
                # st.seat_counts.online expected; guard if missing
                online = 0
                try:
                    online = int(st.get("seat_counts", {}).get("online", 0))
                except Exception:
                    online = 0
                if online >= int(needed_seats):
                    log(f'Found train "{t.get("trip_number")}" with {online} available seats.')
                    return {
                        "trip_id": st.get("trip_id"),
                        "trip_route_id": st.get("trip_route_id"),
                        "train_label": t.get("trip_number") or t.get("train_model") or None,
                        "boarding_point_id": (t.get("boarding_points") or [{}])[0].get("trip_point_id"),
                    }
                else:
                    log(f'Skipping train "{t.get("trip_number")}": not enough seats (found {online}, need {needed_seats}).')
    return None


# ---------------- CONFIG ----------------
CONFIG = {
    "MOBILE": os.getenv("MOBILE", "") or "",
    "PASSWORD": os.getenv("PASSWORD", "") or "",
    "FROM_CITY": os.getenv("FROM_CITY", "") or "",
    "TO_CITY": os.getenv("TO_CITY", "") or "",
    "DATE_OF_JOURNEY": os.getenv("DATE_OF_JOURNEY", "") or "",
    "SEAT_CLASS": (os.getenv("SEAT_CLASS", "S_CHAIR") or "S_CHAIR").lower(),
    "NEED_SEATS": int(os.getenv("NEED_SEATS", "1")) if (os.getenv("NEED_SEATS", "") or "").strip() != "" else 1,
    "TRAIN_NAME": (os.getenv("TRAIN_NAME", "") or "").lower(),
    "PREFERRED_COACHES": [c.strip().lower() for c in os.getenv("PREFERRED_COACHES", "").split(",")] if os.getenv("PREFERRED_COACHES", "") else [],
    "PREFERRED_SEATS": [s.strip() for s in os.getenv("PREFERRED_SEATS", "").split(",")] if os.getenv("PREFERRED_SEATS", "") else [],
    "REQUEST_TIMEOUT": int(os.getenv("REQUEST_TIMEOUT", "20000")),
    "DEVICE_ID": os.getenv("DEVICE_ID", "4004028937") or "4004028937",
    "REFERER": os.getenv("REFERER", "https://eticket.railway.gov.bd/") or "https://eticket.railway.gov.bd/",
    "BASE": os.getenv("BASE", "https://railspaapi.shohoz.com/v1.0/web") or "https://railspaapi.shohoz.com/v1.0/web",
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
    token = None
    trip = None
    successfully_reserved: List[str] = []
    rl = None  # removed usage; kept name to minimize diff in error handling
    otp_payload = None  # accessible after OTP verification

    try:
        log("STARTING flow")

        log("1) Signing in...")
        r = axios_req(ENDPOINTS["SIGNIN"], {"mobile_number": CONFIG["MOBILE"], "password": CONFIG["PASSWORD"]}, token=None, method="post", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
        token = None
        if isinstance(r.data, dict):
            token = (r.data.get("data") or {}).get("token")
        if not token:
            raise Exception("Sign-in failed (no token received)")
        log("Signed in.")
        # Ask user whether to proceed after successful sign-in
        proceed_answer = ask_question("Do you want to proceed with the booking? (yes/no): ")
        if (proceed_answer or "").strip().lower() not in ("y", "yes"):
            raise Exception("Booking process aborted by user.")
        
        log(f'2) Searching trips {CONFIG["FROM_CITY"]} -> {CONFIG["TO_CITY"]}...')
        r = axios_req(ENDPOINTS["SEARCH"], {
            "from_city": CONFIG["FROM_CITY"],
            "to_city": CONFIG["TO_CITY"],
            "date_of_journey": CONFIG["DATE_OF_JOURNEY"],
            "seat_class": CONFIG["SEAT_CLASS"],
        }, token=token, method="get", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])

        trains = []
        if isinstance(r.data, dict):
            trains = (r.data.get("data") or {}).get("trains") or []
        if not trains:
            raise Exception("No trains found for this route.")

        if CONFIG["TRAIN_NAME"]:
            trains = [t for t in trains if CONFIG["TRAIN_NAME"] in str((t.get("trip_number") or "")).lower()]
            if not trains:
                raise Exception(f'The specified train "{CONFIG["TRAIN_NAME"]}" was not found.')

        # ---- Top-K candidate probing (parallel) ----
        def _online_seats_for_class(train: Dict[str, Any], seat_class: str) -> int:
            try:
                st_list = train.get("seat_types") or []
                st = next((x for x in st_list if (x.get("type") or "").lower() == seat_class), None)
                return int(((st or {}).get("seat_counts") or {}).get("online", 0))
            except Exception:
                return 0

        sorted_trains = sorted(
            trains,
            key=lambda t: _online_seats_for_class(t, CONFIG["SEAT_CLASS"]),
            reverse=True,
        )
        # Fixed K-probe size (not configurable via env)
        K = 3
        candidates = sorted_trains[:K]

        def _probe_candidate(train: Dict[str, Any]):
            # Raise on not enough, and attach server response to e.details
            st_list = train.get("seat_types") or []
            st = next((x for x in st_list if (x.get("type") or "").lower() == CONFIG["SEAT_CLASS"]), None)
            if not st:
                e = Exception("seat_class not found on candidate")
                raise e
            resp = axios_req(ENDPOINTS["SEAT_LAYOUT"], {"trip_id": st.get("trip_id"), "trip_route_id": st.get("trip_route_id")}, token=token, method="get", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
            avail = find_available_seats(resp.data if isinstance(resp.data, dict) else {}, CONFIG["NEED_SEATS"], CONFIG["PREFERRED_COACHES"], CONFIG["PREFERRED_SEATS"])
            if len(avail) >= CONFIG["NEED_SEATS"]:
                return {
                    "trip": {
                        "trip_id": st.get("trip_id"),
                        "trip_route_id": st.get("trip_route_id"),
                        "train_label": train.get("trip_number") or train.get("train_model") or None,
                        "boarding_point_id": ((train.get("boarding_points") or [{}])[0]).get("trip_point_id"),
                    },
                    "available_seats": avail,
                    "raw_seat_layout_response": resp.data,
                }
            e = Exception("Not enough matching seats for this candidate")
            try:
                e.details = resp.data
            except Exception:
                pass
            raise e

        chosen = None
        probe_errors: List[Exception] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(candidates), 4) or 1) as executor:
            futs = [executor.submit(_probe_candidate, t) for t in candidates]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    res = fut.result()
                    if res:
                        chosen = res
                        break
                except Exception as pe:
                    probe_errors.append(pe)

        if not chosen:
            err = Exception(f'No train found with at least {CONFIG["NEED_SEATS"]} available seats of class "{CONFIG["SEAT_CLASS"]}".')
            # propagate one error's server details if present
            for pe in probe_errors:
                det = getattr(pe, "details", None)
                if det is not None:
                    try:
                        err.details = det
                    except Exception:
                        pass
                    break
            raise err

        trip = chosen["trip"]
        available_seats = chosen["available_seats"]
        log("Selected trip:", trip.get("train_label"))

        # Attach detailed error if somehow seats are still insufficient
        if len(available_seats) < CONFIG["NEED_SEATS"]:
            err = Exception(
                f"Could not find enough seats matching preferences. Found {len(available_seats)}, needed {CONFIG['NEED_SEATS']}."
            )
            try:
                err.details = chosen.get("raw_seat_layout_response")
            except Exception:
                pass
            raise err

        # Map ticket_id -> seat_number and prep reservation list
        ticket_id_to_seat_no = {s["ticket_id"]: s["seat_number"] for s in available_seats}
        ticket_ids_to_reserve = [s["ticket_id"] for s in available_seats]
        seat_numbers_found = ", ".join([s["seat_number"] for s in available_seats])
        log(f'Found {len(available_seats)} seats: {seat_numbers_found}')

        log("4) Reserving seats (in parallel)...")

        def _reserve_one(tid: str):
            try:
                rr = axios_req(
                    ENDPOINTS["RESERVE"],
                    {"ticket_id": tid, "route_id": trip["trip_route_id"]},
                    token=token,
                    method="patch",
                    config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"],
                )
                status_bad = getattr(rr, "status", 0) >= 300
                data_has_error = False
                if isinstance(rr.data, dict):
                    data_has_error = bool((rr.data.get("data") or {}).get("error"))
                if status_bad or data_has_error:
                    return (False, tid, rr.data)
                return (True, tid, None)
            except Exception as e:
                return (False, tid, str(e))

        results = []
        max_workers = max(2, min(len(ticket_ids_to_reserve), 8))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_reserve_one, tid) for tid in ticket_ids_to_reserve]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:
                    # Shouldn't happen since _reserve_one catches, but guard anyway
                    results.append((False, "unknown", str(e)))

        successes = [r for r in results if r[0] is True]
        failures = [r for r in results if r[0] is False]
        for ok, tid, _ in successes:
            log(f"  - Successfully reserved ticket ID: {tid}")
        for ok, tid, reason in failures:
            try:
                reason_str = reason if isinstance(reason, str) else json.dumps(reason)
            except Exception:
                reason_str = repr(reason)
            log(f"  - Failed to reserve ticket ID: {tid}. Reason: {reason_str}")

        # Stop-when-enough: require at least NEED_SEATS successes
        if len(successes) < CONFIG["NEED_SEATS"]:
            sample = []
            for _, tid, reason in failures[:3]:
                try:
                    sample.append({"tid": tid, "reason": reason if isinstance(reason, str) else json.dumps(reason)})
                except Exception:
                    sample.append({"tid": tid, "reason": repr(reason)})
            err = Exception(
                f"Only reserved {len(successes)}/{CONFIG['NEED_SEATS']} seat(s). Sample failures: {json.dumps(sample, ensure_ascii=False)}"
            )
            # Attach reservation attempt details (including server responses) for debugging
            try:
                err.details = {
                    "failures": [{"tid": tid, "reason": reason} for _, tid, reason in failures],
                    "reserveResults": [
                        {"ok": ok, "tid": tid, "reason": reason} for (ok, tid, reason) in results
                    ],
                }
            except Exception:
                pass
            raise err

        # Use only the first NEED_SEATS successes
        reserved_tids = [tid for _, tid, _ in successes][: CONFIG["NEED_SEATS"]]
        successfully_reserved[:] = reserved_tids
        reserved_seat_numbers = ", ".join([ticket_id_to_seat_no.get(tid, "?") for tid in reserved_tids])
        log(f"Successfully reserved {len(successes)} seat(s). Proceeding with: {reserved_seat_numbers}")
        # Ask user whether to proceed after successful reservations
        proceed_answer = ask_question("Do you want to proceed to OTP verification? (yes/no): ")
        if (proceed_answer or "").strip().lower() not in ("y", "yes"):
            raise Exception("Booking process aborted by user.")
        log("5) Triggering OTP send...")
        passenger_payload = {"trip_id": trip["trip_id"], "trip_route_id": trip["trip_route_id"], "ticket_ids": successfully_reserved}
        r = axios_req(ENDPOINTS["PASSENGER_DETAILS"], passenger_payload, token=token, method="post", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
        if not (isinstance(r.data, dict) and (r.data.get("data") or {}).get("success")):
            # attach response in message
            raise Exception(f"API error while triggering OTP: {json.dumps(r.data)}")
        log(f'OTP sent to your phone: "{(r.data.get("data") or {}).get("msg")}"')

        otp_verified = False
        main_passenger = None
        last_otp_error = None

        for attempt in range(1, 4):
            otp = ask_question(f"Please enter the OTP you received (Attempt {attempt}/3): ")
            if not otp or not re.match(r"^\d{4,6}$", otp):
                log("Invalid OTP format. Please try again.")
                continue
            log(f"6) Verifying OTP (Attempt {attempt}/3)...")
            otp_payload = dict(passenger_payload)
            otp_payload["otp"] = otp
            r = axios_req(ENDPOINTS["VERIFY_OTP"], otp_payload, token=token, method="post", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
            if isinstance(r.data, dict) and (r.data.get("data") or {}).get("success"):
                main_passenger = (r.data.get("data") or {}).get("user")
                log("✅ OTP Verified for user:", main_passenger.get("name") if isinstance(main_passenger, dict) else main_passenger)
                otp_verified = True
                break
            else:
                last_otp_error = r.data
                if attempt < 3:
                    log("Incorrect OTP. Please try again.")

        if not otp_verified:
            raise Exception(f"OTP verification failed after 3 attempts. Last error: {json.dumps(last_otp_error)}")

        passenger_details = {"pname": [main_passenger.get("name")], "passengerType": ["Adult"], "gender": ["male"]}
        if CONFIG["NEED_SEATS"] > 1:
            log(f'Please enter details for the other {CONFIG["NEED_SEATS"] - 1} passenger(s).')
            for i in range(1, CONFIG["NEED_SEATS"]):
                name = ask_question(f"  - Passenger {i+1} Name: ")
                ptype = ask_question(f"  - Passenger {i+1} Type (Adult/Child): ")
                gender = ask_question(f"  - Passenger {i+1} Gender (Male/Female): ")
                passenger_details["pname"].append(name)
                # Normalize passenger type: accept adult/child/adlt; default to 'adult'
                ptype_l = (ptype or "").strip().lower()
                if ptype_l not in ("adult", "child", "adlt"):
                    passenger_details["passengerType"].append("adult")
                else:
                    passenger_details["passengerType"].append(ptype_l)

                # Normalize gender: accept male/female; default to 'male'
                gender_l = (gender or "").strip().lower()
                if gender_l not in ("male", "female"):
                    passenger_details["gender"].append("male")
                else:
                    passenger_details["gender"].append(gender_l)

        log("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====")
        log(f"Train:          {trip.get('train_label')}")
        log(f"From:           {CONFIG['FROM_CITY']}")
        log(f"To:             {CONFIG['TO_CITY']}")
        log(f"Date:           {CONFIG['DATE_OF_JOURNEY']}")
        log(f"Class:          {CONFIG['SEAT_CLASS']}")
        log(f"Total Seats:    {min(len(successes), CONFIG['NEED_SEATS'])}")
        log(f"Seat Numbers:   {reserved_seat_numbers}")
        log("\nPassengers:")
        for i in range(len(passenger_details["pname"])):
            log(f"  - {passenger_details['pname'][i]} ({passenger_details['passengerType'][i]}, {passenger_details['gender'][i]})")
        log("============================================")

        confirmation = ask_question("Proceed to payment? (yes/no): ")
        if confirmation.lower() not in ("yes", "y"):
            raise Exception("Booking cancelled by user.")

        # ensure otp_payload exists
        if not otp_payload or not otp_payload.get("otp"):
            raise Exception("Internal error: OTP payload missing.")

        log("9) Confirming booking to get payment link...")
        nulls_array = [None] * CONFIG["NEED_SEATS"]
        empty_str_array = [""] * CONFIG["NEED_SEATS"]
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
            "date_of_birth": nulls_array,
            "first_name": nulls_array,
            "last_name": nulls_array,
            "middle_name": nulls_array,
            "nationality": nulls_array,
            "page": empty_str_array,
            "ppassport": empty_str_array,
            "passport_expiry_date": nulls_array,
            "passport_no": empty_str_array,
            "passport_type": nulls_array,
            "visa_expire_date": nulls_array,
            "visa_issue_date": nulls_array,
            "visa_issue_place": nulls_array,
            "visa_no": nulls_array,
            "visa_type": nulls_array,
        }

        r = axios_req(ENDPOINTS["CONFIRM"], confirm_payload, token=token, method="patch", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
        if getattr(r, "status", None) != 200 or not (isinstance(r.data, dict) and (r.data.get("data") or {}).get("redirectUrl")):
            raise Exception(f"Could not get payment URL. Response: {json.dumps(r.data)}")

        payment_url = (r.data.get("data") or {}).get("redirectUrl")
        log("✅ Booking Confirmed!")
        log(f"Opening payment link in your browser: {payment_url}")
        # JS used `open` package which returns a promise; in Python we use webbrowser.open
        try:
            webbrowser.open(payment_url)
        except Exception as e:
            # propagate error (JS used await open(paymentUrl) which might reject)
            raise

        log("\n===== PLEASE COMPLETE YOUR PAYMENT IN THE BROWSER =====")

    except Exception as err:
        # Print error message similar to JS
        err_msg = str(err)
        log(f"\nAn error occurred during the process: {err_msg}")

        # rl no longer used; nothing to close

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
                    try:
                        axios_req(ENDPOINTS["RELEASE_SEAT"], {"ticket_id": tid, "route_id": trip["trip_route_id"]}, token=token, method="patch", config_request_timeout_ms=CONFIG["REQUEST_TIMEOUT"])
                        log(f"    - Released {tid}")
                    except Exception as releaseErr:
                        log(f"    - Failed to release {tid}: {str(releaseErr)}")
                except Exception as e_inner:
                    log(f"    - Failed to release {tid}: {str(e_inner)}")
            log("Release attempts finished.")

        # include details if provided on the error (err.details)
        details = getattr(err, "details", None)
        short_msg = "Booking process failed and has been rolled back."
        if isinstance(details, dict) and details.get("error"):
            code = details.get("error", {}).get("code")
            messages = details.get("error", {}).get("messages")
            msg0 = None
            if isinstance(messages, list) and messages:
                msg0 = messages[0]
            short_msg = f"Booking failed — code {code}: {msg0 or 'Unknown error'}"
        # DEBUG_ERRORS env toggle to include full details payload
        DEBUG_ERRORS = (os.getenv("DEBUG_ERRORS", "") or "").strip().lower()
        if DEBUG_ERRORS in ("1", "true", "yes") and details is not None:
            fatal(short_msg, details)
        else:
            fatal(short_msg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fatal("Interrupted by user. Exiting.")
    except Exception as e:
        # If any unexpected error escapes, print and exit
        fatal(f"An unhandled critical error occurred: {e}", getattr(e, "details", None))
