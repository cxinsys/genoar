import { redirect } from "next/navigation";

// The Browse page has been merged into the dashboard at "/".
// This route is kept as a redirect so existing links/bookmarks (and any
// query filters) continue to work.
export default async function BrowseRedirect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(sp)) {
    if (Array.isArray(value)) {
      for (const v of value) qs.append(key, v);
    } else if (value != null) {
      qs.set(key, value);
    }
  }
  const query = qs.toString();
  redirect(query ? `/?${query}` : "/");
}
