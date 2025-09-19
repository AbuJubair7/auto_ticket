/**
 * rail_booking.js
 * Final version: Improved error handling, robust rollback, OTP scope fixes, and cleanup.
 */

const axios = require("axios");
const readline = require("readline");
require("dotenv").config();

// --- Main Script Flow ---
(async function main() {
  const open = (await import("open")).default;

  const CONFIG = {
    MOBILE: process.env.MOBILE || "",
    PASSWORD: process.env.PASSWORD || "",
    FROM_CITY: process.env.FROM_CITY || "",
    TO_CITY: process.env.TO_CITY || "",
    DATE_OF_JOURNEY: process.env.DATE_OF_JOURNEY || "",
    SEAT_CLASS: (process.env.SEAT_CLASS || "S_CHAIR").toLowerCase(),
    NEED_SEATS: parseInt(process.env.NEED_SEATS, 10) || 1,
    TRAIN_NAME: (process.env.TRAIN_NAME || "").toLowerCase(),
    PREFERRED_COACHES: process.env.PREFERRED_COACHES
      ? process.env.PREFERRED_COACHES.split(",").map((c) =>
          c.trim().toLowerCase()
        )
      : [],
    PREFERRED_SEATS: process.env.PREFERRED_SEATS
      ? process.env.PREFERRED_SEATS.split(",").map((s) => s.trim())
      : [],
    REQUEST_TIMEOUT: 20000,
    DEVICE_ID: "4004028937",
    REFERER: "https://eticket.railway.gov.bd/",
    BASE: "https://railspaapi.shohoz.com/v1.0/web",
  };

  const ENDPOINTS = {
    SIGNIN: `${CONFIG.BASE}/auth/sign-in`,
    SEARCH: `${CONFIG.BASE}/bookings/search-trips-v2`,
    SEAT_LAYOUT: `${CONFIG.BASE}/bookings/seat-layout`,
    RESERVE: `${CONFIG.BASE}/bookings/reserve-seat`,
    RELEASE_SEAT: `${CONFIG.BASE}/bookings/release-seat`,
    PASSENGER_DETAILS: `${CONFIG.BASE}/bookings/passenger-details`,
    VERIFY_OTP: `${CONFIG.BASE}/bookings/verify-otp`,
    CONFIRM: `${CONFIG.BASE}/bookings/confirm`,
  };

  function log(...args) {
    console.log("[rail]", ...args);
  }
  function fatal(err, data) {
    console.error("[rail][FATAL]", err);
    if (data !== undefined)
      console.error("[rail][DETAILS]", JSON.stringify(data, null, 2));
    process.exit(1);
  }
  function createReadline() {
    return readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
  }
  function askQuestion(rl, query) {
    return new Promise((resolve) =>
      rl.question(query, (ans) => resolve(ans.trim()))
    );
  }
  async function axiosReq(url, data = {}, token = null, method = "post") {
    const headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-Device-Id": CONFIG.DEVICE_ID,
      Referer: CONFIG.REFERER,
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    try {
      const opts = {
        url,
        method: method.toLowerCase(),
        headers,
        timeout: CONFIG.REQUEST_TIMEOUT,
        validateStatus: null,
      };
      if (method.toLowerCase() === "get") opts.params = data;
      else opts.data = data;
      const res = await axios(opts);
      return res;
    } catch (e) {
      if (e.code === "ECONNABORTED")
        throw new Error(`Request timeout for ${url}`);
      throw e;
    }
  }
  function findAvailableSeats(
    seatLayoutResponse,
    needed,
    preferredCoaches,
    preferredSeats
  ) {
    const seatLayout = seatLayoutResponse?.data?.seatLayout;
    if (!seatLayout) return [];
    const availableSeats = [];
    const coachesHaveValue = preferredCoaches && preferredCoaches.length > 0;
    const seatsHaveValue = preferredSeats && preferredSeats.length > 0;
    const coachesToSearch = coachesHaveValue
      ? seatLayout.filter((c) =>
          preferredCoaches.includes((c.floor_name || "").toLowerCase())
        )
      : seatLayout;
    const preferredSeatNumbers = seatsHaveValue
      ? new Set(preferredSeats.map(String))
      : null;
    if (coachesHaveValue)
      log(
        "Mode: Searching only in preferred coaches:",
        preferredCoaches.join(", ")
      );
    if (seatsHaveValue)
      log(
        "Mode: Searching only for preferred seat numbers:",
        preferredSeats.join(", ")
      );
    for (const coach of coachesToSearch) {
      for (const row of coach.layout) {
        for (const seat of row) {
          if (availableSeats.length >= needed) return availableSeats;
          if (seat.seat_availability === 1 && seat.seat_number) {
            if (preferredSeatNumbers) {
              const seatNumPart = seat.seat_number.split("-")[1];
              if (preferredSeatNumbers.has(seatNumPart)) {
                availableSeats.push({
                  ticket_id: seat.ticket_id,
                  seat_number: seat.seat_number,
                });
              }
            } else {
              availableSeats.push({
                ticket_id: seat.ticket_id,
                seat_number: seat.seat_number,
              });
            }
          }
        }
      }
    }
    return availableSeats;
  }
  function findTripForSeatClass(trains, seatClass, neededSeats) {
    if (!trains) return null;
    for (const t of trains) {
      if (!Array.isArray(t.seat_types)) continue;
      for (const st of t.seat_types) {
        if ((st.type || "").toLowerCase() === seatClass) {
          if (st.seat_counts.online >= neededSeats) {
            log(
              `Found train "${t.trip_number}" with ${st.seat_counts.online} available seats.`
            );
            return {
              trip_id: st.trip_id,
              trip_route_id: st.trip_route_id,
              train_label: t.trip_number || t.train_model || null,
              boarding_point_id: t.boarding_points[0]?.trip_point_id,
            };
          } else {
            log(
              `Skipping train "${t.trip_number}": not enough seats (found ${st.seat_counts.online}, need ${neededSeats}).`
            );
          }
        }
      }
    }
    return null;
  }

  let token;
  let trip;
  const successfullyReserved = [];
  let rl = null;
  let otpPayload = null; // accessible after OTP verification

  try {
    log("STARTING flow");

    log("1) Signing in...");
    let r = await axiosReq(ENDPOINTS.SIGNIN, {
      mobile_number: CONFIG.MOBILE,
      password: CONFIG.PASSWORD,
    });
    token = r.data?.data?.token;
    if (!token) throw new Error("Sign-in failed (no token received)");
    log("Signed in.");

    rl = createReadline();
    cmd = await askQuestion(
      rl,
      "Do you want to proceed with the booking? (yes/no): "
    );
    if (cmd.toLowerCase() !== "yes" && cmd.toLowerCase() !== "y") {
      throw new Error("Booking process aborted by user.");
    }

    log(`2) Searching trips ${CONFIG.FROM_CITY} -> ${CONFIG.TO_CITY}...`);
    r = await axiosReq(
      ENDPOINTS.SEARCH,
      {
        from_city: CONFIG.FROM_CITY,
        to_city: CONFIG.TO_CITY,
        date_of_journey: CONFIG.DATE_OF_JOURNEY,
        seat_class: CONFIG.SEAT_CLASS,
      },
      token,
      "get"
    );
    let trains = r.data?.data?.trains;
    if (!trains || trains.length === 0)
      throw new Error("No trains found for this route.");
    if (CONFIG.TRAIN_NAME) {
      trains = trains.filter((train) =>
        (train.trip_number || "").toLowerCase().includes(CONFIG.TRAIN_NAME)
      );
      if (trains.length === 0)
        throw new Error(
          `The specified train "${CONFIG.TRAIN_NAME}" was not found.`
        );
    }
    trip = findTripForSeatClass(trains, CONFIG.SEAT_CLASS, CONFIG.NEED_SEATS);
    if (!trip)
      throw new Error(
        `No train found with at least ${CONFIG.NEED_SEATS} available seats of class "${CONFIG.SEAT_CLASS}".`
      );
    log("Selected trip:", trip.train_label);

    log("3) Fetching and filtering seat layout...");
    r = await axiosReq(
      ENDPOINTS.SEAT_LAYOUT,
      { trip_id: trip.trip_id, trip_route_id: trip.trip_route_id },
      token,
      "get"
    );
    const availableSeats = findAvailableSeats(
      r.data,
      CONFIG.NEED_SEATS,
      CONFIG.PREFERRED_COACHES,
      CONFIG.PREFERRED_SEATS
    );

    // =================================================================
    // MODIFIED ERROR LOGIC STARTS HERE
    // =================================================================
    if (availableSeats.length < CONFIG.NEED_SEATS) {
      const error = new Error(
        `Could not find enough seats matching preferences. Found ${availableSeats.length}, needed ${CONFIG.NEED_SEATS}.`
      );
      // Attach the full server response to the error for better debugging
      error.details = r.data;
      throw error;
    }
    // =================================================================
    // MODIFIED ERROR LOGIC ENDS HERE
    // =================================================================

    const seatNumbers = availableSeats.map((s) => s.seat_number).join(", ");
    const ticketIdsToReserve = availableSeats.map((s) => s.ticket_id);
    log(`Found ${availableSeats.length} seats: ${seatNumbers}`);

    log("4) Reserving seats (in parallel)...");
    const reserveResults = await Promise.all(
      ticketIdsToReserve.map(async (tid) => {
        try {
          const rr = await axiosReq(
            ENDPOINTS.RESERVE,
            { ticket_id: tid, route_id: trip.trip_route_id },
            token,
            "patch"
          );
          const failed = rr.status >= 300 || rr?.data?.data?.error;
          if (failed) {
            return { tid, ok: false, reason: rr?.data };
          }
          return { tid, ok: true };
        } catch (e) {
          return { tid, ok: false, reason: e?.message || String(e) };
        }
      })
    );

    const successes = reserveResults.filter((x) => x.ok);
    const failures = reserveResults.filter((x) => !x.ok);

    for (const s of successes) {
      log(`  - Successfully reserved ticket ID: ${s.tid}`);
    }
    for (const f of failures) {
      log(
        `  - Failed to reserve ticket ID: ${f.tid}. Reason: ${
          typeof f.reason === "string" ? f.reason : JSON.stringify(f.reason)
        }`
      );
    }

    // add successful reservations to rollback list
    successfullyReserved.push(...successes.map((s) => s.tid));

    if (failures.length > 0) {
      const summary = failures
        .map((f) => ({ tid: f.tid, reason: f.reason }))
        .slice(0, 3); // cap preview
      throw new Error(
        `Failed to reserve ${failures.length}/${
          ticketIdsToReserve.length
        } seat(s). Sample: ${JSON.stringify(summary)}`
      );
    }
    log(`Successfully reserved all ${successfullyReserved.length} seats.`);

    cmd = await askQuestion(
      rl,
      "Do you want to proceed to OTP verification? (yes/no): "
    );
    if (cmd.toLowerCase() !== "yes" && cmd.toLowerCase() !== "y") {
      throw new Error("Booking process aborted by user.");
    }
    log("5) Triggering OTP send...");
    const passengerPayload = {
      trip_id: trip.trip_id,
      trip_route_id: trip.trip_route_id,
      ticket_ids: successfullyReserved,
    };
    r = await axiosReq(
      ENDPOINTS.PASSENGER_DETAILS,
      passengerPayload,
      token,
      "post"
    );
    if (!r?.data?.data?.success)
      throw new Error(
        `API error while triggering OTP: ${JSON.stringify(r.data)}`
      );
    log(`OTP sent to your phone: "${r.data.data.msg}"`);

    let otpVerified = false;
    let mainPassenger;
    let lastOtpError = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      const otp = await askQuestion(
        rl,
        `Please enter the OTP you received (Attempt ${attempt}/3): `
      );
      if (!otp || !/^\d{4,6}$/.test(otp)) {
        log("Invalid OTP format. Please try again.");
        continue;
      }
      log(`6) Verifying OTP (Attempt ${attempt}/3)...`);
      // store otpPayload in outer scope so we can use it later
      otpPayload = { ...passengerPayload, otp };
      r = await axiosReq(ENDPOINTS.VERIFY_OTP, otpPayload, token, "post");
      if (r.data?.data?.success) {
        mainPassenger = r.data.data.user;
        log("✅ OTP Verified for user:", mainPassenger.name);
        otpVerified = true;
        break;
      } else {
        lastOtpError = r.data;
        if (attempt < 3) {
          log("Incorrect OTP. Please try again.");
        }
      }
    }
    if (!otpVerified) {
      throw new Error(
        `OTP verification failed after 3 attempts. Last error: ${JSON.stringify(
          lastOtpError
        )}`
      );
    }

    const passengerDetails = {
      pname: [mainPassenger.name],
      passengerType: ["Adult"],
      gender: ["male"],
    };
    if (CONFIG.NEED_SEATS > 1) {
      log(
        `Please enter details for the other ${
          CONFIG.NEED_SEATS - 1
        } passenger(s).`
      );
      for (let i = 1; i < CONFIG.NEED_SEATS; i++) {
        const name = await askQuestion(rl, `  - Passenger ${i + 1} Name: `);
        const type = await askQuestion(
          rl,
          `  - Passenger ${i + 1} Type (Adult/Child): `
        );
        const gender = await askQuestion(
          rl,
          `  - Passenger ${i + 1} Gender (Male/Female): `
        );
        passengerDetails.pname.push(name);
        if (
          type.toLowerCase() != "adult" &&
          type.toLowerCase() != "child" &&
          type.toLowerCase() != "adlt"
        )
          passengerDetails.passengerType.push("adult");
        else passengerDetails.passengerType.push(type.toLowerCase());
        if (gender.toLowerCase() != "male" && gender.toLowerCase() != "female")
          passengerDetails.gender.push("male");
        else passengerDetails.gender.push(gender.toLowerCase());
      }
    }

    log("\n===== PLEASE REVIEW YOUR BOOKING DETAILS =====");
    log(`Train:          ${trip.train_label}`);
    log(`From:           ${CONFIG.FROM_CITY}`);
    log(`To:             ${CONFIG.TO_CITY}`);
    log(`Date:           ${CONFIG.DATE_OF_JOURNEY}`);
    log(`Class:          ${CONFIG.SEAT_CLASS}`);
    log(`Total Seats:    ${availableSeats.length}`);
    log(`Seat Numbers:   ${seatNumbers}`);
    log("\nPassengers:");
    for (let i = 0; i < passengerDetails.pname.length; i++) {
      log(
        `  - ${passengerDetails.pname[i]} (${passengerDetails.passengerType[i]}, ${passengerDetails.gender[i]})`
      );
    }
    log("============================================");

    const confirmation = await askQuestion(
      rl,
      "Proceed to payment? (yes/no): "
    );
    rl.close();
    rl = null; // mark closed
    if (
      confirmation.toLowerCase() !== "yes" &&
      confirmation.toLowerCase() !== "y"
    ) {
      throw new Error("Booking cancelled by user.");
    }

    // ensure otpPayload exists before using
    if (!otpPayload || !otpPayload.otp) {
      throw new Error("Internal error: OTP payload missing.");
    }

    log("9) Confirming booking to get payment link...");
    const nullsArray = Array(CONFIG.NEED_SEATS).fill(null);
    const emptyStrArray = Array(CONFIG.NEED_SEATS).fill("");
    const confirmPayload = {
      ...passengerPayload,
      otp: otpPayload.otp,
      boarding_point_id: trip.boarding_point_id,
      pname: passengerDetails.pname,
      passengerType: passengerDetails.passengerType,
      gender: passengerDetails.gender,
      pemail: mainPassenger.email,
      pmobile: mainPassenger.mobile,
      contactperson: 0,
      enable_sms_alert: 0,
      seat_class: CONFIG.SEAT_CLASS,
      from_city: CONFIG.FROM_CITY,
      to_city: CONFIG.TO_CITY,
      date_of_journey: CONFIG.DATE_OF_JOURNEY,
      is_bkash_online: true,
      selected_mobile_transaction: 1,
      date_of_birth: nullsArray,
      first_name: nullsArray,
      last_name: nullsArray,
      middle_name: nullsArray,
      nationality: nullsArray,
      page: emptyStrArray,
      ppassport: emptyStrArray,
      passport_expiry_date: nullsArray,
      passport_no: emptyStrArray,
      passport_type: nullsArray,
      visa_expire_date: nullsArray,
      visa_issue_date: nullsArray,
      visa_issue_place: nullsArray,
      visa_no: nullsArray,
      visa_type: nullsArray,
    };
    r = await axiosReq(ENDPOINTS.CONFIRM, confirmPayload, token, "patch");
    if (r.status !== 200 || !r?.data?.data?.redirectUrl)
      throw new Error(
        `Could not get payment URL. Response: ${JSON.stringify(r.data)}`
      );

    const paymentUrl = r.data.data.redirectUrl;
    log("✅ Booking Confirmed!");
    log(`Opening payment link in your browser: ${paymentUrl}`);
    await open(paymentUrl);

    log("\n===== PLEASE COMPLETE YOUR PAYMENT IN THE BROWSER =====");
  } catch (err) {
    log(`\nAn error occurred during the process: ${err.message}`);

    // ensure readline is closed if still open
    try {
      if (rl) {
        rl.close();
        rl = null;
      }
    } catch (e) {
      log("Failed to close readline:", e.message || e);
    }

    if (successfullyReserved.length > 0) {
      log(
        `Attempting to release ${successfullyReserved.length} reserved seat(s)...`
      );

      // release each ticket individually and don't let one failure cancel the others
      const releasePromises = successfullyReserved.map(async (tid) => {
        try {
          log(`  - Releasing ticket ID: ${tid}`);
          if (!ENDPOINTS.RELEASE_SEAT) {
            log(
              "    - RELEASE_SEAT endpoint not configured; skipping release."
            );
            return;
          }
          if (!trip || !trip.trip_route_id) {
            log(
              "    - trip or trip_route_id missing; cannot release ticket, skipping."
            );
            return;
          }
          await axiosReq(
            ENDPOINTS.RELEASE_SEAT,
            { ticket_id: tid, route_id: trip.trip_route_id },
            token,
            "patch"
          );
          log(`    - Released ${tid}`);
        } catch (releaseErr) {
          log(
            `    - Failed to release ${tid}: ${
              releaseErr.message || releaseErr
            }`
          );
        }
      });

      await Promise.all(releasePromises);
      log("Release attempts finished.");
    }

    // include details if provided on the error
    const details = err && err.details ? err.details : undefined;
    // show only server error code + first message (no full details)
    const shortMsg =
      details && details.error
        ? `Booking failed — code ${details.error.code}: ${
            details.error.messages?.[0] ?? "Unknown error"
          }`
        : "Booking process failed and has been rolled back.";

    fatal(shortMsg);
  }
})();
