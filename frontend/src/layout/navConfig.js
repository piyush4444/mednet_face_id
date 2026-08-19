/**
 * navConfig.js — the single source of truth for admin sidebar navigation.
 *
 * Data-only (serialisable): each item carries an ``icon`` *string* resolved
 * to an SVG by the ICONS map in Sidebar.jsx, and an optional ``perm`` used to
 * RBAC-filter the item before render (see useAuth().hasPermission). Groups
 * render as labelled sections. Add a route here and it appears in the nav —
 * no component edits.
 */

export const NAV_GROUPS = [
  {
    label: "Main",
    items: [
      { key: "dashboard", label: "Live View", path: "/", icon: "dashboard", exact: true },
      { key: "frontdesk", label: "Front Desk", path: "/frontdesk", icon: "frontdesk", perm: "frontdesk.operate" },
    ],
  },
  {
    label: "People",
    items: [
      { key: "register", label: "Registration", path: "/register", icon: "register", perm: "faces.enroll" },
      { key: "users", label: "Manage Users", path: "/users", icon: "users", perm: "users.read" },
    ],
  },
  {
    label: "Records",
    items: [
      { key: "history", label: "History", path: "/history", icon: "history", perm: "history.read" },
    ],
  },
  {
    label: "Administration",
    items: [
      { key: "locations", label: "Locations", path: "/locations", icon: "location", perm: "locations.manage" },
      { key: "cameras", label: "Cameras", path: "/cameras", icon: "camera", perm: "cameras.manage" },
      { key: "frontdesk-admin", label: "Departments & Rooms", path: "/frontdesk-admin", icon: "departments", perm: "frontdesk_admin.manage" },
    ],
  },
  {
    label: "System",
    items: [
      { key: "accounts", label: "Accounts & RBAC", path: "/accounts", icon: "accounts", perm: "accounts.manage_staff" },
    ],
  },
];

// Flat lookup (path → label) for breadcrumbs / document titles.
export const NAV_BY_PATH = Object.fromEntries(
  NAV_GROUPS.flatMap((g) => g.items).map((i) => [i.path, i.label]),
);
