import { auth } from "@/auth";
import { redirect } from "next/navigation";

export default async function Home() {
  const session = await auth();
  if (!session) redirect("/login");
  const role = (session as unknown as { role?: "admin" | "member" }).role ?? "member";
  if (role === "admin") redirect("/admin");
  redirect("/runs");
}
