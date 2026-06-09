Getting Started
===============

User Setup
----------

.. code-block:: bash

   ./setup

Common Commands
---------------

.. code-block:: bash

   ./vmctl create configs/devbox.yaml
   ./vmctl destroy devbox
   ./vmssh-admin devbox

Developer Setup
---------------

.. code-block:: bash

   ./setup --dev
   ./test
   ./lint
   make -C docs html
