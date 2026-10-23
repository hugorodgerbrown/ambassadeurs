/*
 * static/js/passkeys.js — passkey sign-in (login page) and adding one (account page).
 *
 * Ported from the Titan template (ADR 0030). Both pages render the passkey
 * controls hidden; this file shows them only where the browser can make or
 * use passkeys, so a browser without WebAuthn sees the email form alone. The
 * server keeps the challenge in the session (accounts/passkeys.py); every
 * request here is same-origin with the CSRF token.
 *
 * The UI is bilingual, so no copy lives in this file: each section carries
 * its translated messages as data-msg-* attributes rendered by Django.
 *
 * Nothing asks for a passkey until the button is pressed: no autofill
 * (conditional mediation) request on page load, which some browsers and
 * password managers show as a prompt before anyone has asked for one.
 */
(function () {
    'use strict';

    const codec = self.WebAuthnJSON;
    const supported = Boolean(self.PublicKeyCredential && navigator.credentials && codec);

    function csrfToken(section) {
        const input = section.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    async function postJSON(section, url, body) {
        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(section) },
            body: body === undefined ? '{}' : JSON.stringify(body),
        });
        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            data = {};
        }
        return { status: response.status, ok: response.ok, data: data };
    }

    function showError(section, message) {
        const el = section.querySelector('[data-passkey-error]');
        if (!el) return;
        el.textContent = message;
        el.hidden = !message;
    }

    /** What to tell the user when a ceremony fails. Empty means say nothing. */
    function describe(section, error) {
        const msg = section.dataset;
        if (!navigator.onLine) return msg.msgOffline;
        if (error && error.name === 'NotAllowedError') return ''; // cancelled, or timed out
        if (error && error.name === 'AbortError') return '';
        if (error && error.name === 'InvalidStateError') return msg.msgExists || msg.msgFailed;
        return msg.msgFailed;
    }

    // ---------- sign in ----------

    async function signIn(section) {
        const options = await postJSON(section, section.dataset.optionsUrl);
        if (!options.ok) throw new Error('options ' + options.status);
        const credential = await navigator.credentials.get({ publicKey: codec.requestOptions(options.data) });
        if (!credential) return;
        const result = await postJSON(section, section.dataset.verifyUrl, {
            credential: codec.credentialToJSON(credential),
        });
        if (result.ok) {
            location.assign(result.data.next);
            return;
        }
        if (result.status === 404 && result.data.credentialId) {
            // Deleted here but still on the device: ask the device to forget it,
            // under the RP ID the ceremony used (it can be a parent domain).
            const PKC = self.PublicKeyCredential;
            if (typeof PKC.signalUnknownCredential === 'function') {
                PKC.signalUnknownCredential({
                    rpId: options.data.rpId || location.hostname,
                    credentialId: result.data.credentialId,
                }).catch(() => undefined);
            }
            showError(section, section.dataset.msgUnknown);
            return;
        }
        if (result.status === 403) {
            showError(section, section.dataset.msgDisabled);
            return;
        }
        showError(section, describe(section, null));
    }

    function setUpSignIn(section) {
        section.hidden = false;
        const button = section.querySelector('[data-passkey-button]');
        button.addEventListener('click', () => {
            showError(section, '');
            button.disabled = true;
            signIn(section)
                .catch((error) => showError(section, describe(section, error)))
                .finally(() => {
                    button.disabled = false;
                });
        });
    }

    // ---------- add a passkey ----------

    async function register(section) {
        const options = await postJSON(section, section.dataset.optionsUrl);
        if (!options.ok) throw new Error('options ' + options.status);
        const credential = await navigator.credentials.create({ publicKey: codec.creationOptions(options.data) });
        const result = await postJSON(section, section.dataset.registerUrl, codec.credentialToJSON(credential));
        if (!result.ok) throw new Error('register ' + result.status);
        location.reload();
    }

    function setUpRegister(section) {
        section.querySelector('[data-passkey-controls]').hidden = false;
        const unsupported = section.querySelector('[data-passkey-unsupported]');
        if (unsupported) unsupported.hidden = true;
        const button = section.querySelector('[data-passkey-button]');
        button.addEventListener('click', () => {
            showError(section, '');
            button.disabled = true;
            register(section)
                .catch((error) => showError(section, describe(section, error)))
                .finally(() => {
                    button.disabled = false;
                });
        });
    }

    if (!supported) return;
    const signInSection = document.querySelector('[data-passkey-sign-in]');
    if (signInSection) setUpSignIn(signInSection);
    const registerSection = document.querySelector('[data-passkey-register]');
    if (registerSection) setUpRegister(registerSection);
})();
