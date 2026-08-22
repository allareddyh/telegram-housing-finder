"""Streamlit UI: Telegram housing-group scanner (last 30 days)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from filters import (
    extract_rent,
    find_locations,
    matches_keywords,
    matches_sender,
    passes_rent_filter,
)
from telegram_client import (
    complete_login,
    create_client,
    fetch_messages,
    is_authorized,
    list_group_dialogs,
    load_credentials,
    send_login_code,
)

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

st.set_page_config(page_title="Telegram Housing Finder", page_icon="🏠", layout="wide")


def save_env_credentials(api_id: str, api_hash: str, session: str = "housing_session") -> None:
    """Write Telegram credentials to .env and refresh process env."""
    api_id = api_id.strip()
    api_hash = api_hash.strip()
    session = (session or "housing_session").strip()
    ENV_PATH.write_text(
        (
            "# Get api_id and api_hash from https://my.telegram.org\n"
            f"TELEGRAM_API_ID={api_id}\n"
            f"TELEGRAM_API_HASH={api_hash}\n"
            f"TELEGRAM_SESSION={session}\n"
        ),
        encoding="utf-8",
    )
    os.environ["TELEGRAM_API_ID"] = api_id
    os.environ["TELEGRAM_API_HASH"] = api_hash
    os.environ["TELEGRAM_SESSION"] = session
    load_dotenv(ENV_PATH, override=True)


def run_async(coro):
    """Run an async coroutine from Streamlit (sync context)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nested loop edge case — create a fresh loop in a thread is heavy;
            # Streamlit typically has no running loop here.
            return asyncio.run(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def get_client():
    api_id, api_hash, session = load_credentials()
    return create_client(api_id, api_hash, session)


async def _check_auth():
    client = get_client()
    try:
        return await is_authorized(client)
    finally:
        await client.disconnect()


async def _send_code(phone: str) -> str:
    client = get_client()
    try:
        return await send_login_code(client, phone)
    finally:
        await client.disconnect()


async def _sign_in(phone: str, code: str, phone_code_hash: str, password: str | None):
    client = get_client()
    try:
        await complete_login(client, phone, code, phone_code_hash, password or None)
    finally:
        await client.disconnect()


async def _load_groups():
    client = get_client()
    try:
        return await list_group_dialogs(client)
    finally:
        await client.disconnect()


async def _fetch_many(targets: list[str | int], days: int) -> list[dict]:
    client = get_client()
    try:
        all_rows: list[dict] = []
        for target in targets:
            rows = await fetch_messages(client, target, days=days)
            all_rows.extend(rows)
        return all_rows
    finally:
        await client.disconnect()


def apply_filters(
    df: pd.DataFrame,
    keywords: list[str],
    locations: list[str],
    min_rent: float | None,
    max_rent: float | None,
    require_rent: bool,
    location_soft: bool,
    require_all_keywords: bool,
    sender_query: str = "",
) -> pd.DataFrame:
    if df.empty:
        return df

    records = []
    for row in df.to_dict(orient="records"):
        text = row.get("text") or ""
        if not matches_keywords(text, keywords, require_all=require_all_keywords):
            continue

        if not matches_sender(
            row.get("sender_name"),
            row.get("sender_username"),
            sender_query,
        ):
            continue

        rent = extract_rent(text)
        if not passes_rent_filter(rent, min_rent, max_rent, require_rent):
            continue

        matched_locs = find_locations(text, locations)
        if locations and not location_soft and not matched_locs:
            continue

        records.append(
            {
                **row,
                "extracted_rent": rent.amount if rent else None,
                "rent_raw": rent.raw if rent else None,
                "matched_locations": ", ".join(matched_locs) if matched_locs else "",
            }
        )

    out = pd.DataFrame(records)
    if not out.empty and "date" in out.columns:
        out = out.sort_values("date", ascending=False)
    return out


# --- Sidebar: credentials status + login ---
st.sidebar.header("Telegram connection")

creds_ok = bool(
    (os.getenv("TELEGRAM_API_ID") or "").strip()
    and (os.getenv("TELEGRAM_API_HASH") or "").strip()
)
if not creds_ok:
    st.sidebar.warning(
        "API credentials are empty. Get them from "
        "[my.telegram.org](https://my.telegram.org) → API development tools."
    )
    with st.sidebar.form("creds_form"):
        api_id_in = st.text_input("API ID", placeholder="12345678")
        api_hash_in = st.text_input("API Hash", type="password")
        save_creds = st.form_submit_button("Save credentials")
    if save_creds:
        if not api_id_in.strip() or not api_hash_in.strip():
            st.sidebar.error("Both API ID and API Hash are required.")
        elif not api_id_in.strip().isdigit():
            st.sidebar.error("API ID must be a number.")
        else:
            save_env_credentials(api_id_in, api_hash_in)
            st.sidebar.success("Saved to .env — continuing…")
            st.rerun()
    st.info(
        "Enter your Telegram **API ID** and **API Hash** in the sidebar "
        "(from https://my.telegram.org), then click **Save credentials**."
    )
    st.stop()

if "authorized" not in st.session_state:
    try:
        st.session_state.authorized = run_async(_check_auth())
    except Exception as e:
        st.session_state.authorized = False
        st.sidebar.warning(f"Could not check session: {e}")

if st.session_state.authorized:
    st.sidebar.success("Logged in (session file found)")
    if st.sidebar.button("Refresh auth status"):
        st.session_state.authorized = run_async(_check_auth())
        st.rerun()
else:
    st.sidebar.warning("Not logged in — complete login below")
    code_sent = bool(st.session_state.get("phone_code_hash"))

    if not code_sent:
        st.sidebar.markdown("**Step 1 — Send login code**")
        with st.sidebar.form("send_code_form"):
            phone = st.text_input(
                "Phone (with country code)",
                value=st.session_state.get("phone", ""),
                placeholder="+15551234567",
            )
            send_clicked = st.form_submit_button("Send code to Telegram")
        if send_clicked:
            if not phone.strip():
                st.sidebar.error("Enter your phone number")
            else:
                try:
                    phone_code_hash = run_async(_send_code(phone.strip()))
                    st.session_state.phone = phone.strip()
                    st.session_state.phone_code_hash = phone_code_hash
                    st.sidebar.success("Code sent — check the Telegram app on your phone.")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Could not send code: {e}")
    else:
        st.sidebar.markdown("**Step 2 — Enter the code Telegram sent you**")
        st.sidebar.caption(f"Phone: `{st.session_state.get('phone', '')}`")
        with st.sidebar.form("verify_code_form"):
            code = st.text_input("Login code", placeholder="12345")
            password = st.text_input(
                "2FA cloud password (only if Telegram asks)",
                type="password",
            )
            verify_clicked = st.form_submit_button("Verify & log in")
        if st.sidebar.button("Resend / use a different phone"):
            st.session_state.pop("phone_code_hash", None)
            st.rerun()
        if verify_clicked:
            phone = st.session_state.get("phone", "")
            phone_code_hash = st.session_state.get("phone_code_hash")
            if not code.strip():
                st.sidebar.error("Paste the login code from Telegram")
            else:
                try:
                    run_async(
                        _sign_in(
                            phone,
                            code.strip(),
                            phone_code_hash,
                            password.strip() or None,
                        )
                    )
                    st.session_state.authorized = True
                    st.session_state.pop("phone_code_hash", None)
                    st.sidebar.success("Login successful")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Login failed: {e}")

st.title("Telegram Housing Finder")
st.caption("Scan housing groups for the last 30 days — filter by keywords, location, and rent.")

if not st.session_state.authorized:
    st.info(
        "Log in via the sidebar. After the code is sent, open Telegram, "
        "copy the login code, paste it under **Step 2**, then click **Verify & log in**."
    )
    st.stop()

# --- Filters ---
st.sidebar.header("Filters")
days = st.sidebar.slider("Days of history", min_value=1, max_value=30, value=30)
keyword_text = st.sidebar.text_area(
    "Keywords (one per line or comma-separated)",
    value="rent, lease, bedroom, available, flat, apartment",
    help="Message must match at least one keyword (unless you require all).",
)
require_all_keywords = st.sidebar.checkbox("Require all keywords", value=False)
require_rent = st.sidebar.checkbox("Require detectable rent", value=False)
location_soft = st.sidebar.checkbox(
    "Locations: highlight only (do not filter)",
    value=False,
    help="When off, messages must mention at least one listed location.",
)


def split_terms(raw: str) -> list[str]:
    parts: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        t = line.strip()
        if t:
            parts.append(t)
    return parts


keywords = split_terms(keyword_text)
# --- Group selection ---
st.subheader("Groups")
manual = st.text_input(
    "Group username(s) or numeric id(s)",
    placeholder="housing_hyd, another_group, -1001234567890",
    help="Comma-separated. Use @username without @, or chat id.",
)

load_groups = st.button("Load my groups / channels from Telegram")
if load_groups:
    with st.spinner("Loading dialogs…"):
        try:
            st.session_state.groups = run_async(_load_groups())
        except Exception as e:
            st.error(f"Failed to load groups: {e}")

groups = st.session_state.get("groups") or []
selected_titles: list[str] = []
if groups:
    labels = [
        f"{g['title']}  (@{g['username']})" if g.get("username") else f"{g['title']}  (id={g['id']})"
        for g in groups
    ]
    selected_labels = st.multiselect("Pick groups from your account", labels)
    selected_titles = selected_labels
else:
    st.caption("Optional: load your groups to pick from a list, or type usernames above.")

fetch = st.button("Fetch messages", type="primary")

if fetch:
    targets: list[str | int] = []
    if manual.strip():
        for part in manual.split(","):
            part = part.strip().lstrip("@")
            if not part:
                continue
            if part.lstrip("-").isdigit():
                targets.append(int(part))
            else:
                targets.append(part)

    if groups and selected_titles:
        label_to_group = {
            (
                f"{g['title']}  (@{g['username']})"
                if g.get("username")
                else f"{g['title']}  (id={g['id']})"
            ): g
            for g in groups
        }
        for label in selected_titles:
            g = label_to_group[label]
            targets.append(g["username"] or g["id"])

    # Dedupe while preserving order
    seen = set()
    unique_targets: list[str | int] = []
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        unique_targets.append(t)

    if not unique_targets:
        st.warning("Enter at least one group username/id or select from the list.")
    else:
        with st.spinner(f"Fetching up to {days} days from {len(unique_targets)} group(s)…"):
            try:
                rows = run_async(_fetch_many(unique_targets, days=days))
                st.session_state.raw_df = pd.DataFrame(rows)
                st.success(f"Fetched {len(rows)} text messages.")
            except Exception as e:
                st.error(f"Fetch failed: {e}")

raw_df: pd.DataFrame = st.session_state.get("raw_df", pd.DataFrame())

if raw_df.empty:
    st.info("No messages loaded yet. Select groups and click Fetch messages.")
    st.stop()

st.subheader("Results")
st.markdown("##### Filter by sender, rent, and location")
fc1, fc2, fc3 = st.columns([1.2, 1.4, 1.2])
with fc1:
    sender_query = st.text_input(
        "Sender name",
        placeholder="Name or @username",
        help="Matches sender display name or username (substring, case-insensitive).",
    )
with fc2:
    location_text = st.text_input(
        "Location",
        value=st.session_state.get("location_filter_text", ""),
        placeholder="Irving, Frisco, McKinney",
        help="Comma-separated. Message must mention at least one (unless soft mode in sidebar).",
    )
    st.session_state.location_filter_text = location_text
with fc3:
    rc1, rc2 = st.columns(2)
    with rc1:
        min_rent_in = st.number_input("Min rent", min_value=0, value=0, step=100)
    with rc2:
        max_rent_in = st.number_input(
            "Max rent",
            min_value=0,
            value=0,
            step=100,
            help="0 = no maximum",
        )

locations = split_terms(location_text)
min_rent = float(min_rent_in) if min_rent_in > 0 else None
max_rent = float(max_rent_in) if max_rent_in > 0 else None

filtered = apply_filters(
    raw_df,
    keywords=keywords,
    locations=locations,
    min_rent=min_rent,
    max_rent=max_rent,
    require_rent=require_rent,
    location_soft=location_soft,
    require_all_keywords=require_all_keywords,
    sender_query=sender_query,
)

c1, c2, c3 = st.columns(3)
c1.metric("Raw messages", len(raw_df))
c2.metric("After filters", len(filtered))
c3.metric("Groups scanned", raw_df["group"].nunique() if "group" in raw_df.columns else 0)

if filtered.empty:
    st.warning(
        "No rows match these filters. Try clearing Location, raising Max rent "
        "(0 = no max), or emptying Sender name."
    )

display_cols = [
    c
    for c in [
        "date",
        "group",
        "sender_name",
        "sender_username",
        "extracted_rent",
        "rent_raw",
        "matched_locations",
        "text",
    ]
    if c in filtered.columns
]

st.dataframe(
    filtered[display_cols] if not filtered.empty else filtered,
    width="stretch",
    height=480,
)

if not filtered.empty:
    csv = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        data=csv,
        file_name="housing_messages.csv",
        mime="text/csv",
    )

with st.expander("Show unfiltered raw messages"):
    st.dataframe(raw_df, width="stretch", height=300)
