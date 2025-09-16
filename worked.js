/**
 * rail_booking.js
 * Final version: Corrects the logic for combining preferred coaches and seats.
 */

const axios = require("axios");
const readline = require("readline");

// --- Main Script Flow ---
(async function main() {
  try {
    const open = (await import("open")).default;

    const CONFIG = {
      MOBILE: process.env.MOBILE || "01854078563",
      PASSWORD: process.env.PASSWORD || "MSDdhoni@7",
      FROM_CITY: process.env.FROM_CITY || "Kurigram", // from city
      TO_CITY: process.env.TO_CITY || "Dhaka", // destination city
      DATE_OF_JOURNEY: process.env.DATE_OF_JOURNEY || "18-Sep-2025", // format: "DD-MMM-YYYY"
      SEAT_CLASS: process.env.SEAT_CLASS || "S_CHAIR", // e.g., "S_CHAIR", "AC_S_CHAIR", "FIRST_CLASS"
      NEED_SEATS: 2, // Number of seats to book
      TRAIN_NAME: "", // e.g., "Banalata" (leave empty for any train)
      PREFERRED_COACHES: [""], // e.g., ['CHA', 'JA'] // leave empty for any coach
      PREFERRED_SEATS: [], // e.g., [31, 32] (numbers only) // leave empty for any seat
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
      PASSENGER_DETAILS: `${CONFIG.BASE}/bookings/passenger-details`,
      VERIFY_OTP: `${CONFIG.BASE}/bookings/verify-otp`,
      CONFIRM: `${CONFIG.BASE}/bookings/confirm`,
    };

    // --- Helper Functions ---
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
    // NEW function with corrected logic for all cases
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

      // Case 1: Both are specified. Find 'needed' seats from the preferred numbers within the preferred coaches.
      if (coachesHaveValue && seatsHaveValue) {
        log(
          `Mode: Searching for ${needed} seats with numbers [${preferredSeats.join(
            ", "
          )}] in coaches [${preferredCoaches.join(", ")}]...`
        );
        const coachesToSearch = seatLayout.filter((c) =>
          preferredCoaches.includes(c.floor_name)
        );
        const preferredSeatNumbers = new Set(preferredSeats.map(String));

        for (const coach of coachesToSearch) {
          for (const row of coach.layout) {
            for (const seat of row) {
              if (availableSeats.length >= needed) return availableSeats;
              const seatNumPart = seat.seat_number.split("-")[1];
              if (
                seat.seat_availability === 1 &&
                preferredSeatNumbers.has(seatNumPart)
              ) {
                availableSeats.push({
                  ticket_id: seat.ticket_id,
                  seat_number: seat.seat_number,
                });
              }
            }
          }
        }
        return availableSeats;
      }

      // Case 2: Only seats are specified. Find 'needed' seats from the preferred numbers in ANY coach.
      if (seatsHaveValue) {
        log(
          `Mode: Searching for ${needed} seats with numbers [${preferredSeats.join(
            ", "
          )}] in ANY coach...`
        );
        const preferredSeatNumbers = new Set(preferredSeats.map(String));
        for (const coach of seatLayout) {
          // Iterate all coaches
          for (const row of coach.layout) {
            for (const seat of row) {
              if (availableSeats.length >= needed) return availableSeats;
              const seatNumPart = seat.seat_number.split("-")[1];
              if (
                seat.seat_availability === 1 &&
                preferredSeatNumbers.has(seatNumPart)
              ) {
                availableSeats.push({
                  ticket_id: seat.ticket_id,
                  seat_number: seat.seat_number,
                });
              }
            }
          }
        }
        return availableSeats;
      }

      // Case 3: Only coaches are specified. Find any 'needed' seats in preferred coaches.
      if (coachesHaveValue) {
        log(
          `Mode: Searching for any ${needed} seats in preferred coaches [${preferredCoaches.join(
            ", "
          )}]...`
        );
        const coachesToSearch = seatLayout.filter((c) =>
          preferredCoaches.includes(c.floor_name)
        );
        for (const coach of coachesToSearch) {
          for (const row of coach.layout) {
            for (const seat of row) {
              if (availableSeats.length >= needed) return availableSeats;
              if (seat.seat_availability === 1 && seat.seat_number) {
                availableSeats.push({
                  ticket_id: seat.ticket_id,
                  seat_number: seat.seat_number,
                });
              }
            }
          }
        }
        return availableSeats;
      }

      // Case 4: Neither is specified. Find any 'needed' seats in any coach.
      log(`Mode: Searching for any ${needed} available seats...`);
      for (const coach of seatLayout) {
        for (const row of coach.layout) {
          for (const seat of row) {
            if (availableSeats.length >= needed) return availableSeats;
            if (seat.seat_availability === 1 && seat.seat_number) {
              availableSeats.push({
                ticket_id: seat.ticket_id,
                seat_number: seat.seat_number,
              });
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
          if (String(st.type).toUpperCase() === seatClass.toUpperCase()) {
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

    log("STARTING flow");

    // Steps 1-2
    log("1) Signing in...");
    let r = await axiosReq(ENDPOINTS.SIGNIN, {
      mobile_number: CONFIG.MOBILE,
      password: CONFIG.PASSWORD,
    });
    const token = r.data?.data?.token;
    if (!token) fatal("Sign-in failed", r.data);
    log("Signed in.");

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
      fatal("No trains found for this route.", r.data);

    if (CONFIG.TRAIN_NAME) {
      log(`Filtering for train: "${CONFIG.TRAIN_NAME}"`);
      trains = trains.filter((train) =>
        train.trip_number.includes(CONFIG.TRAIN_NAME)
      );
      if (trains.length === 0)
        fatal(`The specified train "${CONFIG.TRAIN_NAME}" was not found.`);
    }

    const trip = findTripForSeatClass(
      trains,
      CONFIG.SEAT_CLASS,
      CONFIG.NEED_SEATS
    );
    if (!trip)
      fatal(
        `No train found with at least ${CONFIG.NEED_SEATS} available seats of class "${CONFIG.SEAT_CLASS}".`
      );
    log("Selected trip:", trip.train_label);

    // Step 3: Find available seats
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

    if (availableSeats.length < CONFIG.NEED_SEATS) {
      fatal(
        `Could not find enough seats matching your preferences. Found ${availableSeats.length}, needed ${CONFIG.NEED_SEATS}.`
      );
    }

    const seatNumbers = availableSeats.map((s) => s.seat_number).join(", ");
    const reserved = availableSeats.map((s) => s.ticket_id);
    log(`Found ${availableSeats.length} seats: ${seatNumbers}`);

    // Step 4-10...
    log("4) Reserving seats...");
    for (const tid of reserved) {
      r = await axiosReq(
        ENDPOINTS.RESERVE,
        { ticket_id: tid, route_id: trip.trip_route_id },
        token,
        "patch"
      );
      if (r.status >= 300 || r.data?.data?.error)
        fatal(`Reserve-seat failed for ${tid}`, r.data);
    }
    log(`Reserved ${reserved.length} seats.`);

    const rl = createReadline();
    log("5) Triggering OTP send...");
    const passengerPayload = {
      trip_id: trip.trip_id,
      trip_route_id: trip.trip_route_id,
      ticket_ids: reserved,
    };
    r = await axiosReq(
      ENDPOINTS.PASSENGER_DETAILS,
      passengerPayload,
      token,
      "post"
    );
    if (!r?.data?.data?.success)
      fatal("API error while triggering OTP", r.data);
    log(`OTP sent to your phone: "${r.data.data.msg}"`);

    const otp = await askQuestion(rl, "Please enter the OTP you received: ");
    if (!otp || !/^\d{4,6}$/.test(otp)) fatal("Invalid OTP entered.");

    log(`6) Verifying OTP...`);
    const otpPayload = { ...passengerPayload, otp };
    r = await axiosReq(ENDPOINTS.VERIFY_OTP, otpPayload, token, "post");
    if (!r.data?.data?.success) fatal("OTP verification failed", r.data.data);
    const mainPassenger = r.data.data.user;
    log("OTP Verified for user:", mainPassenger.name);

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
        passengerDetails.passengerType.push(type);
        passengerDetails.gender.push(gender.toLowerCase());
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
    if (
      confirmation.toLowerCase() !== "yes" &&
      confirmation.toLowerCase() !== "y"
    ) {
      log("Booking cancelled by user.");
      process.exit(0);
    }

    log("9) Confirming booking to get payment link...");
    const nullsArray = Array(CONFIG.NEED_SEATS).fill(null);
    const emptyStrArray = Array(CONFIG.NEED_SEATS).fill("");
    const confirmPayload = {
      ...passengerPayload,
      otp,
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
      passport_no: nullsArray,
      passport_type: nullsArray,
      visa_expire_date: nullsArray,
      visa_issue_date: nullsArray,
      visa_issue_place: nullsArray,
      visa_no: nullsArray,
      visa_type: nullsArray,
    };
    r = await axiosReq(ENDPOINTS.CONFIRM, confirmPayload, token, "patch");
    if (!r?.data?.data?.redirectUrl)
      fatal("Could not get payment URL from confirmation response", r.data);

    const paymentUrl = r.data.data.redirectUrl;
    log("✅ Booking Confirmed!");
    log(`Opening payment link in your browser: ${paymentUrl}`);
    await open(paymentUrl);

    log("\n===== PLEASE COMPLETE YOUR PAYMENT IN THE BROWSER =====");
    process.exit(0);
  } catch (err) {
    fatal(err.message || err, err.response ? err.response.data : undefined);
  }
})();
