CRYPTO ALPHA unified member flow

crypto-alpha-login.html
  Member login page. Successful sign-in -> admin-review.html.

admin-review.html
  Verification Console. Requires ca_member_session.
  Header shows the signed-in member account and SIGN OUT.
  SIGN OUT clears ca_member_session and returns to login.

verify.html
  Public exchange UID verification submission page.

The word “admin” remains in the filename for compatibility with the current project,
but the UI/session logic is member-facing. Rename the file to verification-console.html
when deploying if you prefer a cleaner public filename.

Prototype session only:
Use server-side authentication with secure HttpOnly cookies in production.
