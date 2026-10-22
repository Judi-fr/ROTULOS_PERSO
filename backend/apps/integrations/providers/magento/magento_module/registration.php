<?php
/**
 * Rotulos_Envio: shipping labels for Magento 2 (orders sync, print and
 * dispatch from the order grid, checkout rates). See README.md.
 */
use Magento\Framework\Component\ComponentRegistrar;

ComponentRegistrar::register(ComponentRegistrar::MODULE, 'Rotulos_Envio', __DIR__);
