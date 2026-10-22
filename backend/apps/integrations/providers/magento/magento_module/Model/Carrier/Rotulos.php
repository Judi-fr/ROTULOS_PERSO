<?php
namespace Rotulos\Envio\Model\Carrier;

use Magento\Framework\App\CacheInterface;
use Magento\Framework\App\Config\ScopeConfigInterface;
use Magento\Quote\Model\Quote\Address\RateRequest;
use Magento\Quote\Model\Quote\Address\RateResult\ErrorFactory;
use Magento\Quote\Model\Quote\Address\RateResult\MethodFactory;
use Magento\Shipping\Model\Carrier\AbstractCarrier;
use Magento\Shipping\Model\Carrier\CarrierInterface;
use Magento\Shipping\Model\Rate\ResultFactory;
use Magento\Store\Model\ScopeInterface;
use Magento\Store\Model\StoreManagerInterface;
use Psr\Log\LoggerInterface;
use Rotulos\Envio\Model\Client;
use Rotulos\Envio\Model\Config;

/**
 * Checkout rates from the app (the same rates table and rule as every other
 * platform). It sits in the middle of a sale: on any failure or timeout it
 * offers nothing and the other shipping methods stay.
 */
class Rotulos extends AbstractCarrier implements CarrierInterface
{
    const TIMEOUT_SECONDS = 5;
    // Magento asks again on every checkout refresh: keep answers a while.
    const CACHE_SECONDS = 300;
    const LBS_TO_KG = 0.45359237;

    protected $_code = 'rotulos';
    protected $_isFixed = true;

    /** @var ResultFactory */
    private $rateResultFactory;

    /** @var MethodFactory */
    private $rateMethodFactory;

    /** @var Client */
    private $client;

    /** @var Config */
    private $rotulosConfig;

    /** @var CacheInterface */
    private $cache;

    /** @var StoreManagerInterface */
    private $storeManager;

    public function __construct(
        ScopeConfigInterface $scopeConfig,
        ErrorFactory $rateErrorFactory,
        LoggerInterface $logger,
        ResultFactory $rateResultFactory,
        MethodFactory $rateMethodFactory,
        Client $client,
        Config $rotulosConfig,
        CacheInterface $cache,
        StoreManagerInterface $storeManager,
        array $data = []
    ) {
        parent::__construct($scopeConfig, $rateErrorFactory, $logger, $data);
        $this->rateResultFactory = $rateResultFactory;
        $this->rateMethodFactory = $rateMethodFactory;
        $this->client = $client;
        $this->rotulosConfig = $rotulosConfig;
        $this->cache = $cache;
        $this->storeManager = $storeManager;
    }

    public function getAllowedMethods()
    {
        return [$this->_code => $this->getConfigData('title')];
    }

    public function collectRates(RateRequest $request)
    {
        if (!$this->getConfigFlag('active') || !$this->rotulosConfig->isLinked()) {
            return false;
        }
        $currency = (string) $this->storeManager->getStore()->getBaseCurrencyCode();
        $query = [
            'postcode' => (string) $request->getDestPostcode(),
            'country' => (string) $request->getDestCountryId(),
            'weight_kg' => round($this->weightKg((float) $request->getPackageWeight()), 3),
            'cart_total' => round((float) $request->getPackageValueWithDiscount(), 2),
            'currency' => $currency,
        ];
        $rates = $this->fetchRates($query);

        $result = $this->rateResultFactory->create();
        foreach ($rates as $rate) {
            if (!is_array($rate) || !isset($rate['code'], $rate['price'])) {
                continue;
            }
            // A rate in another currency would be charged at the wrong value.
            if (!empty($rate['currency']) && strtoupper((string) $rate['currency']) !== strtoupper($currency)) {
                continue;
            }
            $method = $this->rateMethodFactory->create();
            $method->setCarrier($this->_code);
            $method->setCarrierTitle($this->getConfigData('title'));
            $method->setMethod(preg_replace('/[^a-z0-9_]/', '_', strtolower((string) $rate['code'])));
            $method->setMethodTitle($this->methodTitle($rate));
            $method->setPrice((float) $rate['price']);
            $method->setCost((float) $rate['price']);
            $result->append($method);
        }
        return $result;
    }

    private function weightKg($weight)
    {
        $unit = (string) $this->_scopeConfig->getValue('general/locale/weight_unit', ScopeInterface::SCOPE_STORE);
        return $unit === 'lbs' ? $weight * self::LBS_TO_KG : $weight;
    }

    private function methodTitle(array $rate)
    {
        $name = isset($rate['name']) && $rate['name'] !== '' ? (string) $rate['name'] : (string) $rate['code'];
        $min = $rate['delivery_days_min'] ?? null;
        $max = $rate['delivery_days_max'] ?? null;
        if ($min !== null && $max !== null && $min !== $max) {
            return $name . ' (' . __('%1 to %2 days', $min, $max) . ')';
        }
        $days = $max ?? $min;
        return $days !== null ? $name . ' (' . __('%1 days', $days) . ')' : $name;
    }

    private function fetchRates(array $query)
    {
        $key = 'rotulos_rates_' . sha1(json_encode($query));
        $cached = $this->cache->load($key);
        if ($cached !== false) {
            $decoded = json_decode($cached, true);
            return is_array($decoded) ? $decoded : [];
        }
        $answer = $this->client->post($this->rotulosConfig->ratesUrl(), $query, self::TIMEOUT_SECONDS);
        if ($answer === null || ($answer['_status'] ?? 500) >= 400) {
            // Not cached: the next refresh tries again.
            return [];
        }
        $rates = isset($answer['rates']) && is_array($answer['rates']) ? $answer['rates'] : [];
        $this->cache->save(json_encode($rates), $key, [], self::CACHE_SECONDS);
        return $rates;
    }
}
