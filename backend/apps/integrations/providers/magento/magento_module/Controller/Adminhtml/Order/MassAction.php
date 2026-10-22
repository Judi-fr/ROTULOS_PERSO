<?php
namespace Rotulos\Envio\Controller\Adminhtml\Order;

use Magento\Backend\App\Action;
use Magento\Backend\App\Action\Context;
use Magento\Framework\App\Action\HttpPostActionInterface;
use Magento\Sales\Model\ResourceModel\Order\CollectionFactory;
use Magento\Ui\Component\MassAction\Filter;
use Rotulos\Envio\Model\Client;
use Rotulos\Envio\Model\Config;

/**
 * The order grid mass actions: print labels, pickup manifest, dispatch with
 * your carrier. Asks the app (server to server, signed) for a short-lived
 * link and sends the browser there. Dispatching happens in that request,
 * never when the link is opened.
 */
class MassAction extends Action implements HttpPostActionInterface
{
    const ADMIN_RESOURCE = 'Rotulos_Envio::print';
    const ACTIONS = ['labels', 'manifest', 'dispatch'];
    // Dispatch calls the carrier once per order: give it time.
    const TIMEOUT_SECONDS = ['labels' => 30, 'manifest' => 30, 'dispatch' => 90];
    const MAX_ORDERS = 250;

    /** @var Filter */
    private $filter;

    /** @var CollectionFactory */
    private $collectionFactory;

    /** @var Client */
    private $client;

    /** @var Config */
    private $config;

    public function __construct(
        Context $context,
        Filter $filter,
        CollectionFactory $collectionFactory,
        Client $client,
        Config $config
    ) {
        parent::__construct($context);
        $this->filter = $filter;
        $this->collectionFactory = $collectionFactory;
        $this->client = $client;
        $this->config = $config;
    }

    public function execute()
    {
        $back = $this->resultRedirectFactory->create()->setPath('sales/order/index');
        $type = (string) $this->getRequest()->getParam('type');
        if (!in_array($type, self::ACTIONS, true)) {
            $type = 'labels';
        }
        if (!$this->config->isLinked()) {
            $this->messageManager->addErrorMessage(
                __('This store is not linked to the app yet. In the app, open Stores and click "Check module".')
            );
            return $back;
        }
        $ids = array_map('strval', $this->filter->getCollection($this->collectionFactory->create())->getAllIds());
        if (!$ids) {
            $this->messageManager->addErrorMessage(__('Select at least one order.'));
            return $back;
        }
        if (count($ids) > self::MAX_ORDERS) {
            $this->messageManager->addErrorMessage(__('You can select up to %1 orders at a time.', self::MAX_ORDERS));
            return $back;
        }

        $answer = $this->client->post(
            $this->config->printLinkUrl(),
            ['ids' => array_values($ids), 'action' => $type],
            self::TIMEOUT_SECONDS[$type]
        );
        if ($answer === null) {
            $this->messageManager->addErrorMessage(__('Could not reach the app. Try again in a moment.'));
            return $back;
        }
        if (empty($answer['url'])) {
            $detail = isset($answer['detail']) ? (string) $answer['detail'] : __('Unexpected answer from the app.');
            $this->messageManager->addErrorMessage($detail);
            return $back;
        }
        if (!empty($answer['failed']) && is_array($answer['failed'])) {
            foreach ($answer['failed'] as $item) {
                $this->messageManager->addWarningMessage(
                    __('Order #%1 was not dispatched: %2', $item['number'] ?? '?', $item['detail'] ?? '')
                );
            }
        }
        return $this->resultRedirectFactory->create()->setUrl((string) $answer['url']);
    }
}
