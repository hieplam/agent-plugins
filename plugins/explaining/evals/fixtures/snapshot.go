package checkpoint

import (
	"context"

	log "github.com/sirupsen/logrus"
)

type Snapshot struct {
	ID       string
	Sequence uint64
	Payload  []byte
}

type Store interface {
	Save(ctx context.Context, snap Snapshot) error
}

func persistSnapshot(ctx context.Context, store Store, snap Snapshot) {
	if err := store.Save(ctx, snap); err != nil {
		log.WithFields(log.Fields{
			"snapshot_id": snap.ID,
			"sequence":    snap.Sequence,
		}).Warn("snapshot persist failed; continuing without it")
		return
	}
	log.WithFields(log.Fields{"snapshot_id": snap.ID}).Debug("snapshot persisted")
}
