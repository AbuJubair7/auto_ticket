#!/usr/bin/env python3
"""
rail_booking.py
Final version: Improved error handling, robust rollback, OTP scope fixes, and cleanup.
Mirrored from rail_booking.js to Python. No extras.
"""

import os
import re
import sys
import json
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # dotenv optional; proceed if not available
    pass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuration ---
CONFIG = {
    "MOBILE": os.environ.get("MOBILE", "") or "",
    "PASSWORD": os.environ.get("PASSWORD", "") or "",
    "FROM_CITY": os.environ.get("FROM_CITY", "") or "",
    "TO_CITY": os.environ.get("TO_CITY", "") or "",
    "DATE_OF_JOURNEY": os.environ.get("DATE_OF_JOURNEY", "") or "",
    "SEAT_CLASS": (os.environ.get("SEAT_CLASS", "S_CHAIR") or "S_CHAIR").lower(),
    "NEED_SEATS": int(os.environ.get("NEED_SEATS", "1") or 1),
    "TRAIN_NAME": (os.environ.get("TRAIN_NAME", "") or "").lower(),
    "PREFERRED_COACHES": (
        [c.strip().lower() for c in os.environ.get("PREFERRED_COACHES", "").split(",")]
        if os.environ.get("PREFERRED_COACHES")
        else []
    ),
    "PREFERRED_SEATS": (
        [s.strip() for s in os.environ.get("PREFERRED_SEATS", "").split(",")]
        if os.environ.get("PREFERRED_SEATS")
        else []
    ),
    "REQUEST_TIMEOUT": 20,  # seconds
    "DEVICE_ID": "4004028937",
    "REFERER": "https://eticket.railway.gov.bd/",
    "BASE": "https://railspaapi.shohoz.com/v1.0/web",
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


# Keep-alive requests session
session = requests.Session()
retries = Retry(total=1, backoff_factor=0.1, status_forcelist=[502, 503, 504])
adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)
session.headers.update(
    {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Device-Id": CONFIG["DEVICE_ID"],
        "Referer": CONFIG["REFERER"],
    }
)


def log(*args):
    print("[rail]", *args)


def fatal(err: str, data: Any = None):
    print("[rail][FATAL]", err, file=sys.stderr)
    if data is not None:
        try:
            print("[rail][DETAILS]", json.dumps(data, indent=2), file=sys.stderr)
        except Exception:
            print("[rail][DETAILS]", str(data), file=sys.stderr)
    sys.exit(1)


def ask_question(query: str) -> str:
    try:
        return input(query).strip()
    except EOFError:
        return ""


def axios_req(url: str, data: Optional[Dict] = None, token: Optional[str] = None, method: str = "post"):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        method = method.lower()
        timeout = CONFIG["REQUEST_TIMEOUT"]
        if method == "get":
            r = session.get(url, params=data, headers=headers, timeout=timeout)
        elif method == "patch":
            r = session.patch(url, json=data, headers=headers, timeout=timeout)
        elif method == "post":
            r = session.post(url, json=data, headers=headers, timeout=timeout)
        else:
            r = session.request(method, url, json=data, headers=headers, timeout=timeout)
        # do not raise for status to mimic axios validateStatus: null
        return r
    except requests.exceptions.Timeout:
        raise Exception(f"Request timeout for {url}")
    except Exception as e:
        raise


def find_available_seats(seat_layout_response: Dict, needed: int, preferred_coaches: List[str], preferred_seats: List[str]):
    seatLayout = None
    if isinstance(seat_layout_response, dict):
        seatLayout = seat_layout_response.get("seatLayout") or seat_layout_response.get("seat_layout")
    if not seatLayout:
        return []

    available = []
    coaches_have_value = bool(preferred_coaches)
    seats_have_value = bool(preferred_seats)
    if coaches_have_value:
        coaches_to_search = [c for c in seatLayout if (c.get("floor_name") or "").lower() in preferred_coaches]
    else:
        coaches_to_search = seatLayout
    preferred_seat_numbers = set(preferred_seats) if seats_have_value else None

    if coaches_have_value:
        log("Mode: Searching only in preferred coaches:", ", ".join(preferred_coaches))
    if seats_have_value:
        log("Mode: Searching only for preferred seat numbers:", ", ".join(preferred_seats))

    for coach in coaches_to_search:
        for row in coach.get("layout", []):
            for seat in row:
                if len(available) >= needed:
                    return available
                if seat.get("seat_availability") == 1 and seat.get("seat_number"):
                    if preferred_seat_numbers:
                        seat_num_part = seat.get("seat_number", "").split("-")[-1]
                        if seat_num_part in preferred_seat_numbers:
                            available.append({"ticket_id": seat.get("ticket_id"), "seat_number": seat.get("seat_number")})
                    else:
                        available.append({"ticket_id": seat.get("ticket_id"), "seat_number": seat.get("seat_number")})
    return available


def find_trip_for_seat_class(trains: List[Dict], seat_class: str, needed_seats: int):
    if not trains:
        return None
    for t in trains:
        if not isinstance(t.get("seat_types"), list):
            continue
        for st in t.get("seat_types", []):
            if (st.get("type") or "").lower() == seat_class:
                if (st.get("seat_counts", {}).get("online") or 0) >= needed_seats:
                    log(f'Found train "{t.get("trip_number")}" with {st.get("seat_counts", {}).get("online")} available seats.')
                    return {
                        "trip_id": st.get("trip_id"),
                        "trip_route_id": st.get("trip_route_id"),
                        "train_label": t.get("trip_number") or t.get("train_model"),
                        "boarding_point_id": (t.get("boarding_points") or [{}])[0].get("trip_point_id"),
                    }
                else:
                    log(f'Skipping train "{t.get("trip_number")}": not enough seats (found {st.get("seat_counts", {}).get("online")}, need {needed_seats}).')
    return None


def get_online_seats_for_class(train: Dict, seat_class: str) -> int:
    for x in train.get("seat_types", []):
        if (x.get("type") or "").lower() == seat_class:
            return (x.get("seat_counts") or {}).get("online", 0)
    return 0


def probe_candidate(train: Dict, seat_class: str, token: str):
    st = next((x for x in (train.get("seat_types") or []) if (x.get("type") or "").lower() == seat_class), None)
    if not st:
        err = Exception("seat_class not found on candidate")
        raise err
    resp = axios_req(ENDPOINTS["SEAT_LAYOUT"], {"trip_id": st.get("trip_id"), "trip_route_id": st.get("trip_route_id")}, token, "get")
    try:
        data = resp.json()
    except Exception:
        data = {}
    avail = find_available_seats(data, CONFIG["NEED_SEATS"], CONFIG["PREFERRED_COACHES"], CONFIG["PREFERRED_SEATS"])
    if len(avail) >= CONFIG["NEED_SEATS"]:
        return {
            "trip": {
                "trip_id": st.get("trip_id"),
                "trip_route_id": st.get("trip_route_id"),
                "train_label": train.get("trip_number") or train.get("train_model"),
                "boarding_point_id": (train.get("boarding_points") or [{}])[0].get("trip_point_id"),
            },
            "availableSeats": avail,
            "rawSeatLayoutResponse": data,
        }
    err = Exception("Not enough matching seats for this candidate")
    err.details = data
    raise err


def main():
    token = None
    trip = None
    successfully_reserved: List[Any] = []
    rl_open = True
    otp_payload = None

    try:
        log("STARTING flow")

        log("1) Signing in...")
        r = axios_req(ENDPOINTS["SIGNIN"], {"mobile_number": CONFIG["MOBILE"], "password": CONFIG["PASSWORD"]})
        try:
            rdata = r.json()
        except Exception:
            rdata = {}
        token = rdata.get("data", {}).get("token")
        if not token:
            raise Exception("Sign-in failed (no token received)")
        log("Signed in.")

        cmd = ask_question("Do you want to proceed with the booking? (yes/no): ")
        if cmd.lower() not in ("yes", "y"):
            raise Exception("Booking process aborted by user.")

        log(f"2) Searching trips {CONFIG['FROM_CITY']} -> {CONFIG['TO_CITY']}...")
        r = axios_req(
            ENDPOINTS["SEARCH"],
            {"from_city": CONFIG["FROM_CITY"], "to_city": CONFIG["TO_CITY"], "date_of_journey": CONFIG["DATE_OF_JOURNEY"], "seat_class": CONFIG["SEAT_CLASS"]},
            token,
            "get",
        )
        try:
            rdata = r.json()
        except Exception:
            rdata = {}
        trains = rdata.get("data", {}).get("trains")
        if not trains:
            raise Exception("No trains found for this route.")
        if CONFIG["TRAIN_NAME"]:
            trains = [t for t in trains if CONFIG["TRAIN_NAME"] in ((t.get("trip_number") or "").lower())]
            if not trains:
                raise Exception(f'The specified train "{CONFIG["TRAIN_NAME"]}" was not found.')

        seat_class = CONFIG["SEAT_CLASS"]
        sorted_trains = sorted(trains, key=lambda a: get_online_seats_for_class(a, seat_class), reverse=True)
        K = 3
        candidates = sorted_trains[:K]

        chosen = None
        # probe candidates in parallel and pick first successful
        exceptions = []
        with ThreadPoolExecutor(max_workers=len(candidates) or 1) as ex:
            futures = {ex.submit(probe_candidate, c, seat_class, token): c for c in candidates}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    chosen = res
                    break
                except Exception as e:
                    # collect errors
                    exceptions.append(e)
            # cancel other futures if possible
            for fut in futures:
                if not fut.done():
                    fut.cancel()

        if not chosen:
            # try to extract details from one of the exceptions
            with_details = None
            for e in exceptions:
                if hasattr(e, "details"):
                    with_details = getattr(e, "details")
                    break
            err = Exception(f'No train found with at least {CONFIG["NEED_SEATS"]} available seats of class "{CONFIG["SEAT_CLASS"]}".')
            if with_details:
                err.details = with_details
            raise err

        chosen_trip = chosen["trip"]
        available_seats = chosen["availableSeats"]
        trip = chosen_trip
        log("Selected trip:", trip.get("train_label"))

        if len(available_seats) < CONFIG["NEED_SEATS"]:
            error = Exception(f'Could not find enough seats matching preferences. Found {len(available_seats)}, needed {CONFIG["NEED_SEATS"]}.')
            error.details = chosen.get("rawSeatLayoutResponse")
            raise error

        ticketIdToSeatNo = {s["ticket_id"]: s["seat_number"] for s in available_seats}
        ticketIdsToReserve = [s["ticket_id"] for s in available_seats]
        seatNumbersFound = ", ".join(s["seat_number"] for s in available_seats)
        log(f"Found {len(available_seats)} seats: {seatNumbersFound}")

        log("4) Reserving seats (in parallel)...")
        reserve_results = []
        with ThreadPoolExecutor(max_workers=len(ticketIdsToReserve) or 1) as ex:
            futures = {ex.submit(lambda tid: _reserve_one(tid, trip, token), tid): tid for tid in ticketIdsToReserve}
            for fut in as_completed(futures):
                tid = futures[fut]
                try:
                    res = fut.result()
                    reserve_results.append(res)
                except Exception as e:
                    reserve_results.append({"tid": tid, "ok": False, "reason": str(e)})

        successes = [x for x in reserve_results if x.get("ok")]
        failures = [x for x in reserve_results if not x.get("ok")]

        successfully_reserved.extend([s["tid"] for s in successes])

        for s in successes:
            log(f"  - Successfully reserved ticket ID: {s['tid']}")
        for f in failures:
            reason = f.get("reason")
            if isinstance(reason, str):
                reason_str = reason
            else:
                try:
                    reason_str = json.dumps(reason)
                except Exception:
                    reason_str = str(reason)
            log(f"  - Failed to reserve ticket ID: {f.get('tid')}. Reason: {reason_str}")

        if len(successes) < CONFIG["NEED_SEATS"]:
            summary = [{"tid": f.get("tid"), "reason": f.get("reason")} for f in failures][:3]
            err = Exception(f'Only reserved {len(successes)}/{CONFIG["NEED_SEATS"]} seat(s). Sample failures: {json.dumps(summary)}')
            err.details = {"failures": [{"tid": f.get("tid"), "reason": f.get("reason")} for f in failures], "reserveResults": reserve_results}
            raise err

        reservedSeatNumbers = ", ".join(ticketIdToSeatNo[s["tid"]] for s in successes[:CONFIG["NEED_SEATS"]])
        log(f"Successfully reserved {len(successes)} seat(s). Proceeding with: {reservedSeatNumbers}")

        cmd = ask_question("Do you want to proceed to OTP verification? (yes/no): ")
        if cmd.lower() not in ("yes", "y"):
            raise Exception("Booking process aborted by user.")

        log("5) Triggering OTP send...")
        passenger_payload = {"trip_id": trip["trip_id"], "trip_route_id": trip["trip_route_id"], "ticket_ids": successfully_reserved}
        r = axios_req(ENDPOINTS["PASSENGER_DETAILS"], passenger_payload, token, "post")
        try:
            rdata = r.json()
        except Exception:
            rdata = {}
        if not (rdata.get("data", {}).get("success")):
            raise Exception(f'API error while triggering OTP: {json.dumps(rdata)}')
        log(f'OTP sent to your phone: "{rdata.get("data", {}).get("msg")}"')

        otp_verified = False
        main_passenger = None
        last_otp_error = None
        for attempt in range(1, 4):
            otp = ask_question(f"Please enter the OTP you received (Attempt {attempt}/3): ")
            if not otp or not re.match(r"^\d{4,6}$", otp):
                log("Invalid OTP format. Please try again.")
                continue
            log(f"6) Verifying OTP (Attempt {attempt}/3)...")
            otp_payload = {**passenger_payload, "otp": otp}
            r = axios_req(ENDPOINTS["VERIFY_OTP"], otp_payload, token, "post")
            try:
                rdata = r.json()
            except Exception:
                rdata = {}
            if rdata.get("data", {}).get("success"):
                main_passenger = rdata.get("data", {}).get("user")
                log("✅ OTP Verified for user:", main_passenger.get("name"))
                otp_verified = True
                break
            else:
                last_otp_error = rdata
                if attempt < 3:
                    log("Incorrect OTP. Please try again.")
        if not otp_verified:
            raise Exception(f'OTP verification failed after 3 attempts. Last error: {json.dumps(last_otp_error)}')

        passenger_details = {"pname": [main_passenger.get("name")], "passengerType": ["Adult"], "gender": ["male"]}
        if CONFIG["NEED_SEATS"] > 1:
            log(f'Please enter details for the other {CONFIG["NEED_SEATS"] - 1} passenger(s).')
            for i in range(1, CONFIG["NEED_SEATS"]):
                name = ask_question(f"  - Passenger {i+1} Name: ")
                ptype = ask_question(f"  - Passenger {i+1} Type (Adult/Child): ")
                gender = ask_question(f"  - Passenger {i+1} Gender (Male/Female): ")
                passenger_details["pname"].append(name)
                if ptype.lower() not in ("adult", "child", "adlt"):
                    passenger_details["passengerType"].append("adult")
                else:
                    passenger_details["passengerType"].append(ptype.lower())
                if gender.lower() not in ("male", "female"):
                    passenger_details["gender"].append("male")
                else:
                    passenger_details["gender"].append(gender.lower())

        log("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====")
        log(f"Train:          {trip.get('train_label')}")
        log(f"From:           {CONFIG['FROM_CITY']}")
        log(f"To:             {CONFIG['TO_CITY']}")
        log(f"Date:           {CONFIG['DATE_OF_JOURNEY']}")
        log(f"Class:          {CONFIG['SEAT_CLASS']}")
        log(f"Total Seats:    {min(len(successes), CONFIG['NEED_SEATS'])}")
        log(f"Seat Numbers:   {reservedSeatNumbers}")
        log("\nPassengers:")
        for i in range(len(passenger_details["pname"])):
            log(f"  - {passenger_details['pname'][i]} ({passenger_details['passengerType'][i]}, {passenger_details['gender'][i]})")
        log("============================================")

        confirmation = ask_question("Proceed to payment? (yes/no): ")
        if confirmation.lower() not in ("yes", "y"):
            raise Exception("Booking cancelled by user.")

        if not otp_payload or not otp_payload.get("otp"):
            raise Exception("Internal error: OTP payload missing.")

        log("9) Confirming booking to get payment link...")
        nulls_array = [None] * CONFIG["NEED_SEATS"]
        empty_str_array = [""] * CONFIG["NEED_SEATS"]
        confirm_payload = {
            **passenger_payload,
            "otp": otp_payload.get("otp"),
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
        r = axios_req(ENDPOINTS["CONFIRM"], confirm_payload, token, "patch")
        try:
            rdata = r.json()
        except Exception:
            rdata = {}
        if r.status_code != 200 or not (rdata.get("data", {}).get("redirectUrl")):
            raise Exception(f'Could not get payment URL. Response: {json.dumps(rdata)}')

        payment_url = rdata["data"]["redirectUrl"]
        log("✅ Booking Confirmed!")
        log(f"Opening payment link in your browser: {payment_url}")
        try:
            webbrowser.open(payment_url)
        except Exception:
            log("Failed to open browser automatically. Payment URL:", payment_url)

        log("\n===== PLEASE COMPLETE YOUR PAYMENT IN THE BROWSER =====")

    except Exception as err:
        log("\nAn error occurred during the process:", str(err))

        # Attempt to release reserved seats if any
        if successfully_reserved:
            log(f"Attempting to release {len(successfully_reserved)} reserved seat(s)...")
            release_promises = []
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
                        axios_req(ENDPOINTS["RELEASE_SEAT"], {"ticket_id": tid, "route_id": trip.get("trip_route_id")}, token, "patch")
                        log(f"    - Released {tid}")
                    except Exception as release_err:
                        log(f"    - Failed to release {tid}: {str(release_err)}")
                except Exception as e:
                    log("Failed during release attempt:", str(e))
            log("Release attempts finished.")

        details = getattr(err, "details", None)
        short_msg = None
        if details and isinstance(details, dict) and details.get("error"):
            errcode = details["error"].get("code")
            errmsg = details["error"].get("messages", [None])[0] or "Unknown error"
            short_msg = f"Booking failed — code {errcode}: {errmsg}"
        else:
            short_msg = "Booking process failed and has been rolled back."

        DEBUG_ERRORS = str(os.environ.get("DEBUG_ERRORS", "")).lower()
        debug_enabled = DEBUG_ERRORS in ("1", "true", "yes")
        if debug_enabled and details:
            fatal(short_msg, details)
        else:
            fatal(short_msg)


def _reserve_one(tid, trip, token):
    try:
        rr = axios_req(ENDPOINTS["RESERVE"], {"ticket_id": tid, "route_id": trip.get("trip_route_id")}, token, "patch")
        try:
            rrdata = rr.json()
        except Exception:
            rrdata = {}
        failed = rr.status_code >= 300 or rrdata.get("data", {}).get("error")
        if failed:
            return {"tid": tid, "ok": False, "reason": rrdata}
        return {"tid": tid, "ok": True}
    except Exception as e:
        return {"tid": tid, "ok": False, "reason": str(e)}


if __name__ == "__main__":
    main()
