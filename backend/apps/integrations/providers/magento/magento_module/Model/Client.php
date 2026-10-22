<?php
namespace Rotulos\Envio\Model;

use Magento\Framework\HTTP\Client\CurlFactory;
use Psr\Log\LoggerInterface;

/**
 * Server-to-server requests to the app, signed like the WooCommerce plugin:
 * JSON body with "store" and "ts", base64 HMAC-SHA256 of the body with the
 * store secret in X-Rotulos-Signature. Never throws: a failure is logged and
 * null is returned (an order save or a checkout must never break because of us).
 */
class Client
{
    const SIGNATURE_HEADER = 'X-Rotulos-Signature';

    /** @var Config */
    private $config;

    /** @var CurlFactory */
    private $curlFactory;

    /** @var LoggerInterface */
    private $logger;

    public function __construct(Config $config, CurlFactory $curlFactory, LoggerInterface $logger)
    {
        $this->config = $config;
        $this->curlFactory = $curlFactory;
        $this->logger = $logger;
    }

    /**
     * @param string $url
     * @param array $data
     * @param int $timeout Seconds.
     * @return array|null Decoded answer with its HTTP status in "_status", or null.
     */
    public function post($url, array $data, $timeout)
    {
        if ($url === '' || !$this->config->isLinked()) {
            return null;
        }
        $data['store'] = $this->config->storeId();
        $data['ts'] = time();
        $body = json_encode($data);
        $signature = base64_encode(hash_hmac('sha256', $body, $this->config->secret(), true));
        try {
            $curl = $this->curlFactory->create();
            $curl->setTimeout((int) $timeout);
            $curl->addHeader('Content-Type', 'application/json');
            $curl->addHeader('Accept', 'application/json');
            $curl->addHeader(self::SIGNATURE_HEADER, $signature);
            $curl->post($url, $body);
            $status = (int) $curl->getStatus();
            $answer = json_decode((string) $curl->getBody(), true);
            $answer = is_array($answer) ? $answer : [];
            $answer['_status'] = $status;
            if ($status >= 400) {
                $this->logger->warning('Rotulos_Envio: the app answered HTTP ' . $status . ' to ' . $url);
            }
            return $answer;
        } catch (\Throwable $error) {
            $this->logger->warning('Rotulos_Envio: could not reach the app (' . $url . '): ' . $error->getMessage());
            return null;
        }
    }
}
