import Link from "next/link";
import { redirect } from "next/navigation";
import { isAuthed } from "@/server/auth";

const NAV = [
  ["Dashboard", "/dashboard"],
  ["Users", "/users"],
  ["Subscriptions", "/subscriptions"],
  ["Devices", "/devices"],
  ["Proxies", "/proxies"],
  ["Plans", "/plans"],
  ["Catalog", "/catalog"],
  ["Finance", "/finance"],
  ["Referrals", "/referrals"],
  ["Simulate", "/simulate"],
] as const;

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  if (!(await isAuthed())) redirect("/login");
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 border-r p-4">
        <h2 className="mb-4 text-lg font-semibold">TeleBT Admin</h2>
        <nav className="flex flex-col gap-1 text-sm">
          {NAV.map(([label, href]) => (
            <Link key={href} href={href} className="rounded px-2 py-1.5 hover:bg-muted">
              {label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
