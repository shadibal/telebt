import crypto from "node:crypto";
import { cookies } from "next/headers";

const COOKIE = "telebt_admin";

function sign(value: string, secret: string) {
  return crypto.createHmac("sha256", secret).update(value).digest("hex");
}

export function expectedCookie() {
  const secret = process.env.SESSION_SECRET ?? "";
  const user = process.env.ADMIN_USERNAME ?? "";
  return `${user}.${sign(user, secret)}`;
}

export function isValid(value: string | undefined) {
  if (!value) return false;
  const expected = expectedCookie();
  return (
    value.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(value), Buffer.from(expected))
  );
}

export async function isAuthed() {
  const store = await cookies();
  return isValid(store.get(COOKIE)?.value);
}

export async function setSession() {
  const store = await cookies();
  store.set(COOKIE, expectedCookie(), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
}

export async function clearSession() {
  const store = await cookies();
  store.delete(COOKIE);
}
