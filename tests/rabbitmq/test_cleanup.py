"""A real partially created container must be recovered by the fixture's own label."""
import unittest
from support import Broker, IMAGE, LABEL


class CleanupTests(unittest.TestCase):
    def test_interrupted_creation_is_discovered_and_only_owned_container_removed(self):
        b = Broker()
        identifier = b.run(b.docker+["create","--name",b.name,"--label",LABEL+"="+b.name,IMAGE,"/bin/true"]).stdout.strip()
        # Represents a CLI interruption after Docker created the object, before record().
        try:
            b.close()
            self.assertIsNone(b.inspect("container",identifier,check=False),"unrecorded container from this run survived cleanup")
        finally:
            # Keep even this RED self-cleaning after proving the missing behavior.
            if b.inspect("container",identifier,check=False) is not None:
                b.evidence.mkdir(parents=True,exist_ok=True)
                b.record("container",identifier)
                b.remove("container",identifier)
