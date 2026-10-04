import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";

export function middleware(req: NextRequest) {
  const secret = process.env.SESSION_SECRET ?? "";
  const user = process.env.ADMIN_USERNAME ?? "";
  const expected = `${user}.${crypto.createHmac("sha256", secret).update(user).digest("hex")}`;
  const value = req.cookies.get("telebt_admin")?.value ?? "";
  const ok =
    value.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(value), Buffer.from(expected));
  if (!ok) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/users/:path*", "/catalog/:path*", "/devices/:path*", "/proxies/:path*", "/plans/:path*", "/subscriptions/:path*", "/finance/:path*", "/referrals/:path*", "/simulate/:path*"],
};
