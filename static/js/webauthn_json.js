/*
 * static/js/webauthn_json.js — WebAuthn options and credentials to and from JSON.
 *
 * The server (py_webauthn) speaks JSON with binary fields as base64url; the
 * browser API wants ArrayBuffers. Newer browsers convert natively
 * (PublicKeyCredential.parseCreationOptionsFromJSON, credential.toJSON());
 * these functions do it by hand where they don't. passkeys.js uses them.
 *
 * Copied verbatim from the Titan template (ADR 0030); keep the two in step.
 */
(function () {
  'use strict';

  /** base64url text to an ArrayBuffer. */
  function toBuffer(b64url) {
    const b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  /** An ArrayBuffer (or view) to base64url text, unpadded. */
  function toBase64url(buffer) {
    const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function descriptors(list) {
    return (list || []).map((d) => ({ ...d, id: toBuffer(d.id) }));
  }

  /** Server JSON to the `publicKey` argument of navigator.credentials.create(). */
  function creationOptions(json) {
    const PKC = self.PublicKeyCredential;
    if (PKC && typeof PKC.parseCreationOptionsFromJSON === 'function') {
      return PKC.parseCreationOptionsFromJSON(json);
    }
    return {
      ...json,
      challenge: toBuffer(json.challenge),
      user: { ...json.user, id: toBuffer(json.user.id) },
      excludeCredentials: descriptors(json.excludeCredentials),
    };
  }

  /** Server JSON to the `publicKey` argument of navigator.credentials.get(). */
  function requestOptions(json) {
    const PKC = self.PublicKeyCredential;
    if (PKC && typeof PKC.parseRequestOptionsFromJSON === 'function') {
      return PKC.parseRequestOptionsFromJSON(json);
    }
    return { ...json, challenge: toBuffer(json.challenge), allowCredentials: descriptors(json.allowCredentials) };
  }

  /** A credential from create() or get() to the JSON py_webauthn verifies. */
  function credentialToJSON(credential) {
    if (typeof credential.toJSON === 'function') return credential.toJSON();
    const r = credential.response;
    const response = { clientDataJSON: toBase64url(r.clientDataJSON) };
    if (r.attestationObject) {
      response.attestationObject = toBase64url(r.attestationObject);
      response.transports = typeof r.getTransports === 'function' ? r.getTransports() : [];
    } else {
      response.authenticatorData = toBase64url(r.authenticatorData);
      response.signature = toBase64url(r.signature);
      if (r.userHandle) response.userHandle = toBase64url(r.userHandle);
    }
    return {
      id: credential.id,
      rawId: toBase64url(credential.rawId),
      type: credential.type,
      response: response,
      authenticatorAttachment: credential.authenticatorAttachment || undefined,
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
    };
  }

  self.WebAuthnJSON = Object.freeze({ toBuffer, toBase64url, creationOptions, requestOptions, credentialToJSON });
})();
