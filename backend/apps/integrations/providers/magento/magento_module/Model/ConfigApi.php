<?php
namespace Rotulos\Envio\Model;

use Magento\Framework\App\Cache\TypeListInterface;
use Magento\Framework\App\Config\Storage\WriterInterface;
use Magento\Framework\App\Config\ReinitableConfigInterface;
use Magento\Framework\Encryption\EncryptorInterface;
use Magento\Framework\Exception\InputException;
use Rotulos\Envio\Api\ConfigInterface;

class ConfigApi implements ConfigInterface
{
    /** @var WriterInterface */
    private $writer;

    /** @var EncryptorInterface */
    private $encryptor;

    /** @var TypeListInterface */
    private $cacheTypes;

    /** @var ReinitableConfigInterface */
    private $appConfig;

    public function __construct(
        WriterInterface $writer,
        EncryptorInterface $encryptor,
        TypeListInterface $cacheTypes,
        ReinitableConfigInterface $appConfig
    ) {
        $this->writer = $writer;
        $this->encryptor = $encryptor;
        $this->cacheTypes = $cacheTypes;
        $this->appConfig = $appConfig;
    }

    public function save($storeId, $secret, $webhookUrl, $printLinkUrl, $ratesUrl)
    {
        if (!ctype_digit((string) $storeId) || (string) $secret === '') {
            throw new InputException(__('Missing store or secret.'));
        }
        foreach ([$webhookUrl, $printLinkUrl, $ratesUrl] as $url) {
            if (stripos((string) $url, 'https://') !== 0 && stripos((string) $url, 'http://') !== 0) {
                throw new InputException(__('Invalid URL: %1', $url));
            }
        }
        $this->writer->save(Config::PATH_STORE_ID, (string) $storeId);
        $this->writer->save(Config::PATH_SECRET, $this->encryptor->encrypt((string) $secret));
        $this->writer->save(Config::PATH_WEBHOOK_URL, (string) $webhookUrl);
        $this->writer->save(Config::PATH_PRINT_LINK_URL, (string) $printLinkUrl);
        $this->writer->save(Config::PATH_RATES_URL, (string) $ratesUrl);
        // Without this the new values only show up after a cache flush.
        $this->cacheTypes->cleanType('config');
        $this->appConfig->reinit();
        return Config::VERSION;
    }
}
