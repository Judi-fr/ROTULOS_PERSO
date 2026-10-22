<?php
namespace Rotulos\Envio\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Rotulos\Envio\Model\Client;
use Rotulos\Envio\Model\Config;

/**
 * Tells the app an order was saved so it reads it right away. Only the id
 * travels: the app fetches the order through the REST API. Short timeout,
 * and the periodic sync catches anything that does not arrive.
 */
class OrderSaved implements ObserverInterface
{
    const TIMEOUT_SECONDS = 3;

    /** @var Client */
    private $client;

    /** @var Config */
    private $config;

    public function __construct(Client $client, Config $config)
    {
        $this->client = $client;
        $this->config = $config;
    }

    public function execute(Observer $observer)
    {
        $order = $observer->getEvent()->getOrder();
        if (!$order || !$order->getEntityId() || !$this->config->isLinked()) {
            return;
        }
        $this->client->post(
            $this->config->webhookUrl(),
            [
                'event' => 'order_saved',
                'order_id' => (string) $order->getEntityId(),
                'updated_at' => (string) $order->getUpdatedAt(),
            ],
            self::TIMEOUT_SECONDS
        );
    }
}
