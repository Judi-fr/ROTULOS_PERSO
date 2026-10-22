<?php
namespace Rotulos\Envio\Model;

use Magento\Framework\App\Config\ScopeConfigInterface;
use Magento\Framework\Encryption\EncryptorInterface;

/**
 * Reads the connection settings the app wrote (ConfigApi). Global scope: one
 * Magento installation is one store in the app.
 */
class Config
{
    const VERSION = '1.0.0';
    const PATH_STORE_ID = 'rotulos/connection/store_id';
    const PATH_SECRET = 'rotulos/connection/secret';
    const PATH_WEBHOOK_URL = 'rotulos/connection/webhook_url';
    const PATH_PRINT_LINK_URL = 'rotulos/connection/print_link_url';
    const PATH_RATES_URL = 'rotulos/connection/rates_url';

    /** @var ScopeConfigInterface */
    private $scopeConfig;

    /** @var EncryptorInterface */
    private $encryptor;

    public function __construct(ScopeConfigInterface $scopeConfig, EncryptorInterface $encryptor)
    {
        $this->scopeConfig = $scopeConfig;
        $this->encryptor = $encryptor;
    }

    public function storeId()
    {
        return (string) $this->scopeConfig->getValue(self::PATH_STORE_ID);
    }

    public function secret()
    {
        $stored = (string) $this->scopeConfig->getValue(self::PATH_SECRET);
        return $stored === '' ? '' : (string) $this->encryptor->decrypt($stored);
    }

    public function webhookUrl()
    {
        return (string) $this->scopeConfig->getValue(self::PATH_WEBHOOK_URL);
    }

    public function printLinkUrl()
    {
        return (string) $this->scopeConfig->getValue(self::PATH_PRINT_LINK_URL);
    }

    public function ratesUrl()
    {
        return (string) $this->scopeConfig->getValue(self::PATH_RATES_URL);
    }

    /** True once the app wrote the connection (store and secret). */
    public function isLinked()
    {
        return $this->storeId() !== '' && $this->secret() !== '';
    }
}
