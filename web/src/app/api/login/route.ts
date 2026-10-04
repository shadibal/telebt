import { NextRequest, NextResponse } from "next/server";
import { setSession } from "@/server/auth";

export async function POST(req: NextRequest) {
  const { username, password } = await req.json();
  if (
    username === process.env.ADMIN_USERNAME &&
    password === process.env.ADMIN_PASSWORD
  ) {
    await setSession();
    return NextResponse.json({ ok: true });
  }
  return NextResponse.json({ ok: false }, { status: 401 });
}
