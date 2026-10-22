<?php
namespace Rotulos\Envio\Api;

/**
 * Connection settings written by the app (POST /rest/V1/rotulos/config).
 * The merchant never edits them by hand.
 *
 * @api
 */
interface ConfigInterface
{
    /**
     * Save where the app listens and how to sign requests to it.
     *
     * @param string $storeId Store number in the app.
     * @param string $secret Signing secret (HMAC-SHA256).
     * @param string $webhookUrl Where to send saved orders.
     * @param string $printLinkUrl Where to ask for labels, manifests and dispatch.
     * @param string $ratesUrl Where to ask for checkout rates.
     * @return string Module version.
     */
    public function save($storeId, $secret, $webhookUrl, $printLinkUrl, $ratesUrl);
}
