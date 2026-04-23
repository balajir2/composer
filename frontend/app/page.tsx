import { redirect } from "next/navigation";

export default function Home() {
  // 10c+ will redirect to /runs or /designer based on role.
  redirect("/login");
}
