# Isolated rest/fleet export-form component candidate

Not a release, activation, approval, or airline normative source. Metadata
released_at/released_by follows the existing draft layout; no release act exists.

Based on draft.4. Exactly two tasks are appended to the canonical body:

- `rest_floors/st-export-required-layover-1`: eight independent minute probes
  for max(previous duty, home 720 / outstation 600), including the explicit
  outstation-only transmeridian floor 960 for tz_diff_hours >= 4.
- `fleet_exclusions/st-export-fleet-exclusions-1`: two ordinary T1/T2 flights,
  explicit fleet_dh_marked apply_by_type/deadhead/R-EXCL excluding T2.
  This is not a transportOnly-marking claim.

Both complete component worlds derive from the visible
`duty_limits/st-export-worktime-exact-1` export_form_input. All auxiliary
values remain explicit test inputs, not completed airline data. The new fleet
world explicitly declares T2 class 2 as well as T1 in its own component answer
so the complete conditions input covers both ordinary flight types.

Old tasks/worlds and every other body field remain unchanged. Criteria,
dictionaries, requests and versions are byte copies of draft.4; historical
versions are not new release claims. No support, approval, bindings, results,
release artifacts or activation pointers are created here.

Fingerprint: `e9cb16948ac5c8df07bf09f3a1ddfbe745e39798c797c26bdb4dc87197343aa1`, from the ordinary
template_catalog.canon.fingerprint_of function. Removing exactly the two new
tasks reconstructs the complete draft.4 body. Fingerprint identity is not a
judge result; no stand or tests were run during candidate construction.
